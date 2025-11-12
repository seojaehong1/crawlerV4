import argparse
import csv
import random
import re
import time
from typing import Dict, List, Set, Optional

import pandas as pd
from playwright.sync_api import Playwright, sync_playwright, Browser, Page, BrowserContext


def wait_for_network_idle(page: Page, timeout_ms: int = 3000) -> None:
    start = time.time()
    page.wait_for_load_state("domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass
    finally:
        _ = start


def open_new_context(playwright: Playwright, headless: bool) -> BrowserContext:
    chromium = playwright.chromium
    browser = chromium.launch(headless=headless)
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    context = browser.new_context(
        user_agent=user_agent,
        viewport={"width": 1366, "height": 800},
        locale="ko-KR",
        timezone_id="Asia/Seoul",
        device_scale_factor=1.0,
        has_touch=False,
    )
    return context


def human_delay(base_delay_ms: int = 500) -> None:
    jitter = random.randint(0, base_delay_ms)
    time.sleep((base_delay_ms + jitter) / 1000.0)


def slow_scroll(page: Page, steps: int = 6, step_px: int = 800, base_delay_ms: int = 300) -> None:
    for _ in range(steps):
        # Pass value into the page context properly
        page.evaluate("step => window.scrollBy(0, step)", step_px)
        human_delay(base_delay_ms)


def extract_specs_from_detail(page: Page) -> Dict[str, str]:
    specs: Dict[str, str] = {}
    
    def add_or_append_spec(key: str, value: str):
        """같은 키가 이미 있으면 값을 합치고, 없으면 추가"""
        # 키와 값이 같으면 스킵 (원산지:원산지 같은 무의미한 데이터)
        if key == value:
            return
            
        if key in specs:
            # 이미 저장된 값과 완전히 같으면 중복 추가 안 함
            if specs[key] == value:
                return
            # 새로운 값이 기존 값에 이미 포함되어 있으면 스킵
            if value in specs[key]:
                return
            # 기존 값이 새로운 값에 포함되어 있으면 스킵
            if specs[key] in value:
                return
            # 쉼표로 구분된 값들 체크
            existing_values = [v.strip() for v in specs[key].split(',')]
            if value.strip() not in existing_values:
                specs[key] = f"{specs[key]},{value}"
        else:
            specs[key] = value
    
    # 전체 페이지에서 모든 th/td 쌍 찾기 (테스트 코드에서 검증됨)
    all_tr_elements = page.locator("tr").all()
    for tr in all_tr_elements:
        try:
            ths = tr.locator("th").all()
            tds = tr.locator("td").all()
            
            # th가 1개고 td가 여러 개인 경우 (예: 보관방식 | 상온 ○ 냉장 ○)
            if len(ths) == 1 and len(tds) > 1:
                try:
                    parent_key = ths[0].inner_text().strip()
                    # td들을 순회하며 값 수집 (○ 체크마크가 있는 항목들)
                    for td in tds:
                        value = td.inner_text().strip()
                        # ○ 체크마크는 건너뛰고, 실제 값만 수집
                        if value and value not in ["○", "O", "o", "●"]:
                            # 이 값이 카테고리명일 가능성 (예: 상온, 냉장)
                            add_or_append_spec(parent_key, value)
                except Exception:
                    pass
            
            # 일반적인 1:1 매핑
            for i in range(min(len(ths), len(tds))):
                try:
                    key = ths[i].inner_text().strip()
                    value = tds[i].inner_text().strip()
                    
                    if not key:
                        continue
                    
                    # 값 정리
                    value = value.split("인증번호 확인")[0].strip()
                    value = value.split("바로가기")[0].strip()
                    value = re.sub(r'\s*\([^)]*\)', '', value)
                    
                    if value:
                        add_or_append_spec(key, value)
                except Exception:
                    continue
        except Exception:
            continue

    return specs


def click_detail_tab_if_present(page: Page) -> None:
    labels = ["상세정보", "상세 사양", "상세스펙", "상세 스펙", "스펙", "사양"]
    for label in labels:
        button = page.get_by_role("button", name=label)
        if button.count() > 0:
            try:
                button.first.click(timeout=2000)
                wait_for_network_idle(page)
                return
            except Exception:
                pass
        link = page.get_by_role("link", name=label)
        if link.count() > 0:
            try:
                link.first.click(timeout=2000)
                wait_for_network_idle(page)
                return
            except Exception:
                pass

    for label in labels:
        locator = page.locator(f"text={label}")
        if locator.count() > 0:
            try:
                locator.first.click(timeout=2000)
                wait_for_network_idle(page)
                return
            except Exception:
                pass


def collect_product_links_from_category(page: Page, max_per_page: Optional[int]) -> List[str]:
    # Prefer product title anchors inside list cards; avoid option/price links
    selectors = [
        "li.prod_item div.prod_info a.prod_link",
        "li.prod_item .prod_name a",
        "div.prod_info a.prod_link",
        "a[href*='/product/']",
        "a[href*='product/view.html']",
    ]
    links: List[str] = []
    seen: Set[str] = set()
    for selector in selectors:
        # ensure list is rendered and visible before grabbing
        if page.locator(selector).count() == 0:
            continue
        for a in page.locator(selector).all():
            try:
                href = a.get_attribute("href")
                text = (a.inner_text() or "").strip()
            except Exception:
                continue
            if not href:
                continue
            if href.startswith("javascript:"):
                continue
            # danawa product details live under prod.danawa.com/product/... or similar
            if "danawa" not in href and not href.startswith("/"):
                continue
            if href in seen:
                continue
            # Skip obvious non-title links like 가격비교/옵션 등
            lowered = text.lower()
            if any(x in lowered for x in ["가격", "비교", "옵션", "구성"]):
                continue
            seen.add(href)
            links.append(href)
            if max_per_page and len(links) >= max_per_page:
                return links
    return links


def paginate_category(page: Page, current_url: str, page_num: int) -> bool:
    # 다나와 SPA 페이지네이션 대응
    # movePage(N) JS 함수 직접 실행
    # 자동 클릭 처리
    
    try:
        # 페이지 번호 클릭 시도
        page_buttons = page.locator(f"a.num[onclick*='movePage({page_num})']")
        if page_buttons.count() > 0:
            print(f"  movePage({page_num}) 버튼 클릭 시도...")
            page_buttons.first.click()
            wait_for_network_idle(page)
            return True

        # movePage JS 함수가 존재한다면 직접 호출
        if page.evaluate("typeof movePage === 'function'"):
            print(f"  movePage({page_num}) 직접 실행...")
            page.evaluate(f"movePage({page_num})")
            wait_for_network_idle(page)
            return True

        # 버튼이 없으면 다음 그룹(> 버튼) 클릭으로 전환
        next_group = page.locator("a.edge_nav.nav_next, a[class*='nav_next'], a[onclick*='movePage']").last
        if next_group.count() > 0:
            print(f"  다음 페이지 그룹으로 이동 시도...")
            next_group.click()
            wait_for_network_idle(page)

            # 다시 movePage 시도
            page_buttons = page.locator(f"a.num[onclick*='movePage({page_num})']")
            if page_buttons.count() > 0:
                page_buttons.first.click()
                wait_for_network_idle(page)
                return True

        print(f"  movePage({page_num}) 실패 — 페이지 버튼 또는 함수 호출 불가.")
        return False

    except Exception as e:
        print(f"  페이지네이션 중 오류 발생: {e}")
        return False


def learn_checkmark_patterns(
    category_url: str,
    max_pages: int,
    max_items_per_page: Optional[int],
    headless: bool,
    max_total_items: Optional[int],
    base_delay_ms: int,
) -> List[str]:
    """Pass 1: 모든 상품을 빠르게 스캔하여 체크마크 항목들을 수집"""
    print("\n=== PASS 1: 데이터 구조 학습 중 ===")
    checkmark_items = []
    
    with sync_playwright() as p:
        context = open_new_context(p, headless=headless)
        page = context.new_page()
        page.set_default_timeout(10000)
        
        page.goto(category_url)
        wait_for_network_idle(page)
        slow_scroll(page)
        human_delay(base_delay_ms)
        
        items_scanned = 0
        
        for page_index in range(max_pages):
            try:
                print(f"  페이지 {page_index + 1}/{max_pages} 스캔 중...")
                product_links = collect_product_links_from_category(page, max_items_per_page)
                print(f"    - {len(product_links)}개 링크 발견")
                
                if not product_links:
                    break
                
                for link in product_links:
                    if max_total_items and items_scanned >= max_total_items:
                        break
                    
                    try:
                        detail_page = context.new_page()
                        detail_page.set_default_timeout(15000)
                        detail_page.goto(link, wait_until="domcontentloaded", timeout=15000)
                        wait_for_network_idle(detail_page)
                        
                        # 체크마크 항목만 빠르게 수집
                        specs = extract_specs_from_detail(detail_page)
                        for key, value in specs.items():
                            if value.strip() in ["○", "O", "o", "●"]:
                                if key not in checkmark_items:
                                    checkmark_items.append(key)
                        
                        detail_page.close()
                        items_scanned += 1
                        
                    except Exception as e:
                        print(f"    경고: {link[:50]}... 스캔 실패 - {e}")
                        continue
                
                if max_total_items and items_scanned >= max_total_items:
                    break
                
                # 다음 페이지로 이동
                if page_index < max_pages - 1:
                    next_page_num = page_index + 2
                    moved = paginate_category(page, category_url, next_page_num)
                    if not moved:
                        break
                    slow_scroll(page)
                    human_delay(base_delay_ms)
                    
            except Exception as e:
                print(f"    페이지 {page_index + 1} 스캔 중 오류: {e}")
                continue
        
        context.browser.close()
    
    print(f"  [완료] {items_scanned}개 상품 스캔 완료, {len(checkmark_items)}개 체크마크 항목 발견")
    return checkmark_items


def analyze_and_create_mapping(checkmark_items: List[str]) -> Dict[str, str]:
    """수집된 체크마크 항목들을 분석하여 자동 매핑 생성"""
    print("\n=== 패턴 분석 중 ===")
    auto_mapping = {}
    
    for item in checkmark_items:
        category = None
        
        # 패턴 기반 카테고리 추론
        if "단계" in item or item == "프레":
            category = "단계"
        elif item == "분유":
            category = "품목"
        elif item in ["일반분유", "특수분유", "산양분유", "조제분유"]:
            category = "종류"
        elif "분유" in item:
            category = "종류"  # 기타 ~분유는 종류
        elif item.endswith("개월~") or item.endswith("개월"):
            category = "최소연령"
        elif item in ["분말", "액상", "미음", "죽", "진밥", "아기밥"]:
            category = "형태"
        elif item in ["상온", "냉장", "냉동"]:
            category = "보관방식"
        elif item in ["파우치", "플라스틱병", "병", "캔"]:
            category = "포장용기"
        elif "이유식" in item or item in ["양념", "반찬", "아기국", "수제이유식"]:
            category = "품목"
        elif item in ["국내산", "수입산"]:
            category = "원산지"
        elif "인증" in item:
            category = "인증"
        
        if category:
            auto_mapping[item] = category
    
    # 카테고리별로 그룹화하여 출력
    category_groups = {}
    for item, cat in auto_mapping.items():
        if cat not in category_groups:
            category_groups[cat] = []
        category_groups[cat].append(item)
    
    print("  발견된 데이터 구조:")
    for cat, items in sorted(category_groups.items()):
        print(f"    - {cat}: {', '.join(items[:5])}" + (f" 외 {len(items)-5}개" if len(items) > 5 else ""))
    
    return auto_mapping


def crawl_category(
    category_url: str,
    output_csv: str,
    max_pages: int,
    max_items_per_page: Optional[int],
    headless: bool,
    max_total_items: Optional[int] = None,
    base_delay_ms: int = 500,
    long_format: bool = False,
) -> None:
    # === PASS 1: 데이터 구조 학습 ===
    checkmark_items = learn_checkmark_patterns(
        category_url=category_url,
        max_pages=max_pages,
        max_items_per_page=max_items_per_page,
        headless=headless,
        max_total_items=max_total_items,
        base_delay_ms=base_delay_ms,
    )
    
    # 패턴 분석 및 자동 매핑 생성
    learned_mapping = analyze_and_create_mapping(checkmark_items)
    
    print(f"\n=== PASS 2: 실제 데이터 크롤링 시작 (완성된 매핑 적용) ===\n")
    
    # === PASS 2: 실제 크롤링 ===
    with sync_playwright() as p:
        context = open_new_context(p, headless=headless)
        page = context.new_page()
        page.set_default_timeout(10000)

        page.goto(category_url)
        wait_for_network_idle(page)
        slow_scroll(page)
        human_delay(base_delay_ms)

        all_rows: List[Dict[str, str]] = []
        all_keys: Set[str] = set()

        for page_index in range(max_pages):
            try:
                print(f"페이지 {page_index + 1}/{max_pages} 크롤링 중...")
                product_links = collect_product_links_from_category(page, max_items_per_page)
                print(f"  - {len(product_links)}개 링크 발견")
                
                if not product_links:
                    print(f"  - 페이지 {page_index + 1}에 제품이 없습니다. 종료합니다.")
                    break
                
                # 현재 페이지의 첫 번째 상품명 저장 (페이지 상태 확인용)
                first_product_name_before = ""
                try:
                    first_product = page.locator("li.prod_item .prod_name, li.prod_item a.prod_link").first
                    if first_product.count() > 0:
                        first_product_name_before = first_product.inner_text().strip()
                        print(f"  [페이지 상태] 현재 페이지 첫 번째 상품: {first_product_name_before[:50]}")
                except:
                    pass
                
                for idx, link in enumerate(product_links, 1):
                    if max_total_items and len(all_rows) >= max_total_items:
                        print(f"최대 아이템 수({max_total_items})에 도달했습니다.")
                        break
                    
                    try:
                        print(f"  [{len(all_rows) + 1}] {link[:80]}... 크롤링 중...")
                        detail_page = context.new_page()
                        detail_page.set_default_timeout(15000)  # 타임아웃 증가
                        try:
                            detail_page.goto(link, wait_until="domcontentloaded", timeout=15000)
                            wait_for_network_idle(detail_page)
                            slow_scroll(detail_page, steps=4, step_px=900, base_delay_ms=base_delay_ms)
                            click_detail_tab_if_present(detail_page)
                            specs = extract_specs_from_detail(detail_page)
                            title = ""
                            try:
                                title = detail_page.title() or ""
                            except Exception as e:
                                print(f"    경고: 제목 추출 실패 - {e}")
                                pass
                            
                            # 스펙 정보를 하나의 문자열로 합치기
                            spec_parts = []
                            certification_items = []  # 인증 타입 (예: HACCP인증, 적합성평가인증)
                            certification_info_items = []  # 인증 세부정보 (예: 인증번호)
                            registration_date = ""  # 등록년월일
                            
                            # 키 이름 단순화 매핑 (테스트 코드에서 검증됨)
                            key_simplification = {
                                "재료 종류": "재료",
                                "반찬종류": "종류",
                            }
                            
                            # Pass 1에서 학습한 매핑 사용 (learned_mapping)
                            # 기본 매핑도 유지 (학습되지 않은 항목들을 위해)
                            base_mapping = {
                                "국내산": "원산지",
                                "수입산": "원산지",
                                "국물조림용": "용도",
                                "비빔무침용": "용도",
                            }
                            # 학습된 매핑과 병합 (학습된 것이 우선)
                            category_mapping = {**base_mapping, **learned_mapping}
                            
                            for key, value in specs.items():
                                if not value or not value.strip():
                                    continue
                                
                                # 키 이름 단순화
                                original_key = key
                                key = key_simplification.get(key, key)
                                
                                # 키 이름에서 [] 괄호 제거
                                key = key.replace('[', '').replace(']', '')
                                
                                # 키와 값이 같으면 스킵 (원산지:원산지 같은 무의미한 데이터)
                                if key == value or original_key == value:
                                    continue
                                
                                # 값 정리
                                clean_value = value.strip()
                                # 인증번호 확인 버튼 텍스트 제거
                                clean_value = clean_value.split("인증번호 확인")[0].strip()
                                # 괄호와 그 안의 모든 내용 제거 (닫힌/안 닫힌 괄호 모두 처리)
                                clean_value = re.sub(r'\s*\([^)]*\)', '', clean_value)  # 닫힌 괄호
                                clean_value = re.sub(r'\s*\([^)]*$', '', clean_value)  # 닫히지 않은 괄호 (끝까지)
                                clean_value = re.sub(r'\s*\([^)]*', '', clean_value)    # 열린 괄호부터 끝까지
                                # "제조사 웹사이트" 같은 텍스트 직접 제거
                                clean_value = clean_value.replace("제조사 웹사이트", "").strip()
                                clean_value = clean_value.replace("웹사이트", "").strip()
                                # "바로가기" 관련 텍스트 제거
                                clean_value = clean_value.split("바로가기")[0].strip()
                                # 불필요한 공백 정리
                                clean_value = re.sub(r'\s+', ' ', clean_value).strip()
                                
                                if not clean_value:
                                    continue
                                
                                # 등록년월일/등록년월 처리
                                if "등록년월" in key or "등록일" in key:
                                    registration_date = clean_value
                                    continue
                                
                                # 인증정보 처리 - "인증정보" 섹션의 인증들 (HACCP인증 등)
                                # 체크마크(○)로 표시되는 경우가 많음
                                if key == "인증정보" or ("인증" in key and clean_value in ["○", "O", "o", "●"]):
                                    # HACCP인증 같은 키는 인증정보에 추가
                                    if "HACCP" in key or key == "HACCP인증":
                                        if key not in certification_info_items:
                                            certification_info_items.append(key)
                                        continue
                                
                                # 인증번호 처리
                                if "인증번호" in key:
                                    if clean_value not in certification_info_items:
                                        certification_info_items.append(clean_value)
                                    continue
                                
                                # 무첨가 관련 키 처리 - "無첨가" 섹션으로 변환
                                additive_keys = ["합성보존료", "합성착색료", "합성감미료", "보존료", "착색료", "감미료"]
                                if key in additive_keys:
                                    if clean_value not in ["○", "O", "o", "●", "무첨가", "없음"]:
                                        # 화학물질 이름이 나오면 無첨가 섹션으로 이동
                                        key = "無첨가"
                                
                                # 의미없는 값 체크
                                meaningless_values = [
                                    "상세설명 / 판매 사이트 문의",
                                    "상세설명",
                                    "판매 사이트 문의",
                                    "인증번호 확인"
                                ]
                                is_meaningless = clean_value in meaningless_values or any(mv in clean_value for mv in ["상세설명 / 판매 사이트 문의"])
                                
                                # 체크 표시(○)인 경우 키 이름을 값으로 사용
                                check_marks = ["○", "O", "o", "●"]
                                if clean_value in check_marks:
                                    # HACCP인증은 인증정보에 추가
                                    if "HACCP" in key or key == "HACCP인증":
                                        if key not in certification_info_items:
                                            certification_info_items.append(key)
                                    # 기타 인증은 인증 목록에 추가
                                    elif "인증" in key:
                                        if key not in certification_items:
                                            certification_items.append(key)
                                    else:
                                        # 패턴 기반 자동 매핑
                                        category = None
                                        if key in category_mapping:
                                            category = category_mapping[key]
                                        else:
                                            # 패턴으로 카테고리 추론
                                            if "단계" in key or key == "프레":
                                                category = "단계"
                                            elif "분유" in key:
                                                category = "품목"
                                            elif key.endswith("개월~") or key.endswith("개월"):
                                                category = "최소연령"
                                            elif key in ["분말", "액상", "미음", "죽", "진밥", "아기밥"]:
                                                category = "형태"
                                            elif key in ["상온", "냉장", "냉동"]:
                                                category = "보관방식"
                                            elif key in ["파우치", "플라스틱병"]:
                                                category = "포장용기"
                                        
                                        if category:
                                            # 같은 카테고리가 이미 있는지 확인
                                            existing_entry = None
                                            for part in spec_parts:
                                                if part.startswith(f"{category}:"):
                                                    existing_entry = part
                                                    break
                                            
                                            if existing_entry:
                                                # 이미 있으면 값을 합침
                                                existing_value = existing_entry.split(":", 1)[1]
                                                new_value = f"{existing_value},{key}"
                                                spec_parts.remove(existing_entry)
                                                spec_parts.append(f"{category}:{new_value}")
                                            else:
                                                # 없으면 새로 추가
                                                spec_parts.append(f"{category}:{key}")
                                        # 패턴에도 없으면 스킵
                                # 인증 관련 항목들을 따로 모으기 (HACCP은 제외)
                                elif "인증" in key and "HACCP" not in key:
                                    # 인증 키 이름 자체를 인증 목록에 추가 (값이 의미없어도)
                                    cert_name = key  # "적합성평가인증", "안전확인인증" 등
                                    if cert_name not in certification_items:
                                        certification_items.append(cert_name)
                                else:
                                    # 일반 스펙 항목 - 의미없는 값은 스킵
                                    if not is_meaningless:
                                        # 키와 값이 같은 경우 처리
                                        if key == clean_value and key in category_mapping:
                                            # 매핑된 카테고리 사용
                                            category = category_mapping[key]
                                            # 같은 카테고리가 이미 있는지 확인
                                            existing_entry = None
                                            for part in spec_parts:
                                                if part.startswith(f"{category}:"):
                                                    existing_entry = part
                                                    break
                                            
                                            if existing_entry:
                                                # 이미 있으면 값을 합침
                                                existing_value = existing_entry.split(":", 1)[1]
                                                new_value = f"{existing_value},{key}"
                                                spec_parts.remove(existing_entry)
                                                spec_parts.append(f"{category}:{new_value}")
                                            else:
                                                # 없으면 새로 추가
                                                spec_parts.append(f"{category}:{key}")
                                        else:
                                            # 일반적인 경우
                                            spec_parts.append(f"{key}:{clean_value}")
                            
                            # 인증 항목이 있으면 합쳐서 추가
                            if certification_items:
                                cert_str = ",".join(certification_items)
                                spec_parts.append(f"인증:{cert_str}")
                            
                            # 인증정보 항목이 있으면 추가
                            if certification_info_items:
                                cert_info_str = ",".join(certification_info_items)
                                spec_parts.append(f"인증정보:{cert_info_str}")
                            
                            # 등록년월일 추가
                            if registration_date:
                                spec_parts.append(f"등록년월일:{registration_date}")
                            
                            detail_info = "/".join(spec_parts)
                            row = {"상품명": title, "URL": link, "상세정보": detail_info}
                            all_rows.append(row)
                            print(f"    완료! (총 {len(all_rows)}개 수집)")
                        except Exception as e:
                            print(f"    오류: {link} 크롤링 실패 - {e}")
                            # 실패한 경우에도 빈 행 추가는 하지 않음
                        finally:
                            try:
                                detail_page.close()
                            except:
                                pass
                        
                        # 상세 페이지를 닫은 후 목록 페이지 상태 확인 및 복구
                        try:
                            human_delay(base_delay_ms)  # 기본 딜레이 사용
                            
                            # 메인 페이지의 현재 URL 확인
                            current_url = page.url
                            print(f"    [페이지 복구] 현재 메인 페이지 URL: {current_url[:80]}")
                            
                            # 메인 페이지가 상세 페이지(/info/)로 이동했는지 확인
                            if "/info/" in current_url or "pcode=" in current_url:
                                print(f"    [페이지 복구] 메인 페이지가 상세 페이지로 이동함! 목록 페이지로 복귀...")
                                # 목록 페이지로 다시 이동
                                page.goto(category_url, wait_until="domcontentloaded", timeout=10000)
                                wait_for_network_idle(page)
                                human_delay(1500)
                                
                                # 현재 페이지 번호로 이동
                                if page_index > 0:
                                    print(f"    [페이지 복구] 페이지 {page_index + 1}로 이동...")
                                    paginate_category(page, category_url, page_index + 1)
                                    wait_for_network_idle(page)
                                    human_delay(1500)
                            
                            # 현재 목록 페이지의 첫 번째 상품명 확인
                            current_first_product = ""
                            try:
                                first_product_check = page.locator("li.prod_item .prod_name, li.prod_item a.prod_link").first
                                if first_product_check.count() > 0:
                                    current_first_product = first_product_check.inner_text().strip()
                                    print(f"    [페이지 복구] 현재 첫 번째 상품: {current_first_product[:50]}")
                            except:
                                pass
                            
                            # 첫 번째 상품명이 다르거나 없으면 페이지 상태가 변경된 것
                            if first_product_name_before:
                                if not current_first_product or current_first_product != first_product_name_before:
                                    print(f"    [페이지 복구] 페이지 상태가 변경됨. 복구 중...")
                                    print(f"    [페이지 복구] 예상: {first_product_name_before[:50]}")
                                    print(f"    [페이지 복구] 현재: {current_first_product[:50] if current_first_product else '(없음)'}")
                                    
                                    # 목록 페이지로 다시 이동
                                    page.goto(category_url, wait_until="domcontentloaded", timeout=10000)
                                    wait_for_network_idle(page)
                                    human_delay(1500)
                                    
                                    # 현재 페이지 번호로 이동
                                    if page_index > 0:
                                        paginate_category(page, category_url, page_index + 1)
                                        wait_for_network_idle(page)
                                        human_delay(1500)
                                        
                                        # 복구 후 첫 번째 상품명 확인
                                        first_product_after_recover = ""
                                        try:
                                            first_product_recover = page.locator("li.prod_item .prod_name, li.prod_item a.prod_link").first
                                            if first_product_recover.count() > 0:
                                                first_product_after_recover = first_product_recover.inner_text().strip()
                                                print(f"    [페이지 복구] 복구 후: {first_product_after_recover[:50]}")
                                        except:
                                            pass
                        except Exception as e:
                            print(f"    경고: 목록 페이지 상태 확인 실패 - {e}")
                        
                        human_delay(base_delay_ms)
                    except Exception as e:
                        print(f"  오류: 페이지 생성 실패 - {e}")
                        continue
                
                if max_total_items and len(all_rows) >= max_total_items:
                    print(f"최대 아이템 수({max_total_items})에 도달했습니다.")
                    break
                    
                if page_index < max_pages - 1:
                    print(f"  다음 페이지로 이동 시도...")
                    # 목록 페이지로 다시 이동 (상세 페이지로 이동했을 수 있음)
                    try:
                        current_url = page.url
                        print(f"  [다음 페이지 이동] 현재 URL: {current_url[:80]}")
                        
                        # 메인 페이지가 상세 페이지(/info/)로 이동했는지 확인
                        if "/info/" in current_url or "pcode=" in current_url:
                            print(f"  [다음 페이지 이동] 메인 페이지가 상세 페이지로 이동함! 목록 페이지로 복귀...")
                            page.goto(category_url, wait_until="domcontentloaded", timeout=10000)
                            wait_for_network_idle(page)
                            human_delay(1000)
                            # 현재 페이지 번호로 이동
                            paginate_category(page, category_url, page_index + 1)
                            wait_for_network_idle(page)
                            human_delay(1000)
                        elif category_url not in current_url and "list" not in current_url:
                            print(f"  [다음 페이지 이동] 목록 페이지가 아님. 복귀...")
                            page.goto(category_url, wait_until="domcontentloaded", timeout=10000)
                            wait_for_network_idle(page)
                            human_delay(1000)
                            # 현재 페이지 번호로 이동
                            paginate_category(page, category_url, page_index + 1)
                            wait_for_network_idle(page)
                            human_delay(1000)
                    except Exception as e:
                        print(f"  [다음 페이지 이동] 경고: {e}")
                        # 오류 발생 시에도 목록 페이지로 이동 시도
                        try:
                            page.goto(category_url, wait_until="domcontentloaded", timeout=10000)
                            wait_for_network_idle(page)
                            human_delay(1000)
                        except:
                            pass
                    
                    next_page_num = page_index + 2  # 다음 페이지 번호 (1부터 시작)
                    moved = paginate_category(page, category_url, next_page_num)
                    if not moved:
                        print(f"  다음 페이지로 이동할 수 없습니다. 종료합니다.")
                        break
                    slow_scroll(page)
                    human_delay(base_delay_ms)
            except Exception as e:
                print(f"페이지 {page_index + 1} 처리 중 오류 발생: {e}")
                # 계속 진행
                if page_index < max_pages - 1:
                    try:
                        next_page_num = page_index + 2
                        paginate_category(page, category_url, next_page_num)
                    except:
                        pass

        # 모든 상세 정보를 하나의 컬럼에 저장
        fieldnames = ["상품명", "URL", "상세정보"]
        with open(output_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in all_rows:
                writer.writerow({key: row.get(key, "") for key in fieldnames})

        context.browser.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Danawa category crawler -> CSV")
    parser.add_argument("--category-url", required=True, help="Danawa category URL (list view)")
    parser.add_argument("--output", default="danawa_output.csv", help="Output CSV filepath")
    parser.add_argument("--pages", type=int, default=1, help="Max pages to crawl")
    parser.add_argument("--items-per-page", type=int, default=0, help="Max items per page (0 for all)")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    parser.add_argument("--max-total-items", type=int, default=0, help="Stop after N items across pages (0=unlimited)")
    parser.add_argument("--delay-ms", type=int, default=1000, help="Base human-like delay in ms (기본값: 1000ms)")
    parser.add_argument("--long-format", action="store_true", help="Export as rows: 상품명,URL,key,value")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    crawl_category(
        category_url=args.category_url,
        output_csv=args.output,
        max_pages=args.pages,
        max_items_per_page=(args.items_per_page or None),
        headless=args.headless,
        max_total_items=(args.max_total_items or None),
        base_delay_ms=args.delay_ms,
        long_format=args.long_format,
    )


if __name__ == "__main__":
    main()

