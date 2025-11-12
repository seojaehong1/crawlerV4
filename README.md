# 다나와 카테고리 크롤러 실행 방법

## 1. 가상환경 활성화

```powershell
cd C:\Users\DU\Desktop\1112
.\venv\Scripts\activate
```

## 2. 요구 패키지 설치 (최초 1회)

```powershell
pip install -r require.txt
playwright install
```

## 3. 크롤러 실행

분유 카테고리에서 최대 30개의 상품을 수집하려면:

```powershell
python test.py --category-url "https://prod.danawa.com/list/?cate=16249091&15main_16_02" --pages 1 --items-per-page 30 --headless
```

- `--items-per-page` 값을 조절하면 수집할 상품 수를 변경할 수 있습니다.
- 브라우저 화면을 보면서 확인하려면 `--headless` 옵션을 제거하세요.

## 4. 결과

- 수집된 데이터는 `danawa_output.csv` 등 CSV 파일로 저장됩니다.
- 코드 변경 사항은 `코드추가 및 수정 부분.html` 파일에서 확인할 수 있습니다.

