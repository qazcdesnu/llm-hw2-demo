# Issues

← [CLAUDE.md](../CLAUDE.md) · 관련: [roadmap](roadmap.md) · [ongoing](ongoing.md)

각 ID에는 앵커가 있어 `[I-7](issue.md#i-7)`처럼 링크할 수 있다.

문제점, 리스크, 결정이 필요한 사항. 해결되면 상태와 해결 방법을 적는다.

| ID | 등록일 | 상태 | 내용 | 해결 / 결정 |
|---|---|---|---|---|
| <a id="i-1"></a>I-1 | 2026-10-26 | resolved | 속성 수가 질문 예산(4회)과 비슷하면 IG와 Random·Fixed의 차이가 거의 나지 않음 | 헤드폰 속성 8개 정의(질문 가능 7개, brand 제외) > 질문 예산 4회 |
| <a id="i-2"></a>I-2 | 2026-10-26 | resolved | 가격 결측률이 높을 수 있음 | 전체의 78%가 결측. 가격 있는 상품(+리뷰 100개 이상)만 후보로 쓰고, 분위 기준 3구간(~$20 / $20~60 / $60~)으로 나눔 |
| <a id="i-10"></a>I-10 | 2026-10-26 | open | 대화 견고성 부족: "$30 미만"을 $20~60으로 반영, "일단 목록 볼래" 무시, "잘 몰라"에 같은 질문 반복, "노캔이 뭔데"를 다른 상품으로 오분류. 파서에 대화 행위(모름·되묻기·바로 보기) 칸이 없고 가격이 구간으로만 표현되며 반복 탈출 규칙이 없음 | [roadmap Phase 3++](roadmap.md#phase-3-대화-견고성-모름되묻기바로-보기숫자-가격) |
| <a id="i-9"></a>I-9 | 2026-10-26 | open | 속성 추출 오류 사례: "Open Ear Headphones, Air Conduction" 상품이 form_factor=in_ear, "Over Ear Bud" 이어버드가 over_ear (Amazon details의 Form Factor 값 자체가 틀림). IG 데모에서 추천에 그대로 노출됨 | 수동 검증(40개)에서 오류율 측정 후, 제목 키워드가 details와 충돌하면 제목 우선 등 규칙 보완 검토 |
| <a id="i-8"></a>I-8 | 2026-10-26 | open | "noise cancelling"만 적힌 상품은 ANC 여부가 애매함. 넓은 정규식은 정밀도 약 12%(표본 25개 중 3개)라 기각, 해당 상품은 unknown 처리 → 카탈로그 150개 중 38개가 ANC unknown. 실제 ANC 제품(예: Sony WF-SP800N)도 여기 포함 | 150개 규모라 LLM 추출(gpt-5-nano, 상품당 1회)이나 수동 검증으로 확정 |
| <a id="i-7"></a>I-7 | 2026-10-26 | open | mic·water_resistant는 키워드가 있으면 yes, 없으면 unknown이라 "no"가 하나도 없음 (mic yes 74% / unknown 26%, 방수 yes 31% / unknown 69%). unknown은 답변 가능도가 균등이라 IG가 낮게 나옴 | ANC처럼 `default: "no"`를 줄지, [수동 검증](validation/headphones_validation.csv)에서 "언급 없음 = 없음"이 맞는지 확인 후 결정 |
| <a id="i-6"></a>I-6 | 2026-10-26 | resolved | ANC 상품이 후보 집합의 약 10%뿐이라 그대로 뽑으면 ANC 질문의 IG가 낮음 (10% → 0.47 bit) | 후보: 층화 샘플링으로 비율 조정 / 실제 분포 유지. 실제 분포 유지로 결정 (카탈로그 150개 중 ANC yes 15개). 드문 속성은 맥락 추론 기능([roadmap Phase 3+](roadmap.md#phase-3-기능-고도화-암묵적-선호-추론과-추천-보정))으로 보완 |
| <a id="i-3"></a>I-3 | 2026-10-26 | resolved | `unknown` 속성, "상관없음" 답변, 응답 노이즈를 처리할 가능도 모델이 정의되지 않음 | 일치 c / 불일치 (1−c)/(k−1) / unknown 1/k, 명시 답 c = 1−ε(0.05), 상관없음은 belief 불변 + 재질문 안 함. [IG 엔진 1절](ig-engine.md#1-답변-가능도-모델) |
| <a id="i-5"></a>I-5 | 2026-10-26 | resolved | 기본 모델을 가장 저렴한 `gpt-5-nano` + `minimal`로 정했으나 실제 품질 미확인 | 파서 평가 42개 케이스로 비교 → **`gpt-5-mini` + `low`** (3회 연속 42/42, 1천 턴당 $0.89). nano+minimal은 추정 0/5, nano+low는 맥락 있는 위험 요청을 놓침, `gpt-4.1-nano`는 폐기됨(404). [LLM 파서 3절](llm-parser.md#3-평가) |
| <a id="i-4"></a>I-4 | 2026-10-26 | open | Turn-AUC 정의, Fixed baseline 질문 순서 기준이 정해지지 않음 | [roadmap Phase 4](roadmap.md#phase-4-정량-평가) 전에 확정. 참고: 예비 확인에서 "추천 3개 중 조건 위반 없는 비율"은 IG가 낮게 나옴(조건을 더 좁혀 만족 상품이 3개 미만인 경우를 채우기 때문). 조건 충족률을 "만족 상품이 있을 때 Top-1이 만족하는가" 등으로 정의할지 결정 필요 ([예비 결과](ig-engine.md#6-예비-확인-결과-2026-10-26)) |
