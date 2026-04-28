# 추천 식단 전용 테이블 마이그레이션 가이드

## 📋 개요

추천 식단 데이터를 체계적으로 관리하기 위한 전용 테이블을 추가합니다.

### 새로운 테이블

1. **DietPlan**: 식단 메타데이터 (BMR, TDEE, 목표 칼로리 등)
2. **DietPlanMeal**: 끼니별 상세 정보
3. **v_diet_plan_summary**: 식단 요약 뷰 (조회용)

---

## 🚀 마이그레이션 실행

### 1단계: DB 백업 (권장)

```bash
mysqldump -u root -p your_database > backup_before_diet_plan_$(date +%Y%m%d).sql
```

### 2단계: 마이그레이션 실행

```bash
cd backend/migrations
mysql -u root -p your_database < create_diet_plan_tables.sql
```

또는 MySQL Workbench에서:
```sql
source C:/Users/hyuk/kcal_project/food/backend/migrations/create_diet_plan_tables.sql
```

### 3단계: 테이블 생성 확인

```sql
-- 테이블 확인
SHOW TABLES LIKE 'Diet%';

-- 구조 확인
DESC DietPlan;
DESC DietPlanMeal;

-- 뷰 확인
SELECT * FROM v_diet_plan_summary LIMIT 1;
```

---

## 📊 테이블 구조

### DietPlan (식단 메타데이터)

| 컬럼명 | 타입 | 설명 |
|--------|------|------|
| diet_plan_id | VARCHAR(50) PK | 식단 ID (plan_xxx) |
| user_id | BIGINT FK | 사용자 ID |
| plan_name | VARCHAR(100) | 식단 이름 (예: "고단백 식단") |
| description | TEXT | 식단 설명 |
| bmr | DECIMAL(10,2) | 기초대사량 (kcal/day) |
| tdee | DECIMAL(10,2) | 1일 총 에너지 소비량 |
| target_calories | DECIMAL(10,2) | 목표 칼로리 |
| health_goal | ENUM | 건강 목표 (gain/maintain/loss) |
| total_calories | DECIMAL(10,2) | 식단 총 칼로리 |
| total_protein | DECIMAL(10,2) | 식단 총 단백질 (g) |
| total_carb | DECIMAL(10,2) | 식단 총 탄수화물 (g) |
| total_fat | DECIMAL(10,2) | 식단 총 지방 (g) |
| created_at | DATETIME | 생성일시 |
| is_active | BOOLEAN | 현재 따르고 있는 식단 여부 |

### DietPlanMeal (끼니별 상세)

| 컬럼명 | 타입 | 설명 |
|--------|------|------|
| meal_id | BIGINT PK AUTO_INCREMENT | 끼니 ID |
| diet_plan_id | VARCHAR(50) FK | 식단 ID |
| meal_type | ENUM | 끼니 타입 (breakfast/lunch/dinner/snack) |
| meal_name | VARCHAR(200) | 끼니 이름 |
| food_description | TEXT | 음식 설명 |
| ingredients | JSON | 재료 목록 (배열) |
| calories | DECIMAL(10,2) | 칼로리 (kcal) |
| protein | DECIMAL(10,2) | 단백질 (g) |
| carb | DECIMAL(10,2) | 탄수화물 (g) |
| fat | DECIMAL(10,2) | 지방 (g) |
| consumed | BOOLEAN | 섭취 여부 |
| consumed_at | DATETIME | 섭취 일시 |
| history_id | BIGINT FK | 연결된 섭취 기록 ID |

---

## 🔍 샘플 쿼리

### 저장된 식단 목록 조회

```sql
SELECT 
    dp.diet_plan_id,
    dp.plan_name,
    dp.target_calories,
    dp.health_goal,
    dp.created_at,
    COUNT(dpm.meal_id) AS total_meals,
    SUM(CASE WHEN dpm.consumed = TRUE THEN 1 ELSE 0 END) AS consumed_meals
FROM DietPlan dp
LEFT JOIN DietPlanMeal dpm ON dp.diet_plan_id = dpm.diet_plan_id
WHERE dp.user_id = 1
GROUP BY dp.diet_plan_id
ORDER BY dp.created_at DESC;
```

### 특정 식단의 끼니 상세

```sql
SELECT 
    meal_type,
    meal_name,
    calories,
    protein,
    carb,
    fat,
    consumed
FROM DietPlanMeal
WHERE diet_plan_id = 'plan_1732012345678'
ORDER BY FIELD(meal_type, 'breakfast', 'lunch', 'dinner', 'snack');
```

### 식단 진행률 확인 (뷰 활용)

```sql
SELECT * 
FROM v_diet_plan_summary 
WHERE user_id = 1 
ORDER BY created_at DESC;
```

---

## 🔄 롤백 (필요 시)

마이그레이션을 되돌리려면:

```sql
-- 뷰 삭제
DROP VIEW IF EXISTS v_diet_plan_summary;

-- 테이블 삭제 (외래키 때문에 순서 중요)
DROP TABLE IF EXISTS DietPlanMeal;
DROP TABLE IF EXISTS DietPlan;
```

---

## ✅ 마이그레이션 검증

### 1. 테이블 생성 확인

```bash
mysql> SHOW TABLES LIKE 'Diet%';
+---------------------------+
| Tables_in_db (Diet%)      |
+---------------------------+
| DietPlan                  |
| DietPlanMeal              |
+---------------------------+
```

### 2. 외래키 확인

```sql
SELECT 
    TABLE_NAME,
    COLUMN_NAME,
    CONSTRAINT_NAME,
    REFERENCED_TABLE_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = 'your_database'
AND TABLE_NAME IN ('DietPlan', 'DietPlanMeal')
AND REFERENCED_TABLE_NAME IS NOT NULL;
```

### 3. 샘플 데이터 삽입 테스트

```sql
-- DietPlan 삽입
INSERT INTO DietPlan (
    diet_plan_id, user_id, plan_name, description,
    bmr, tdee, target_calories, health_goal,
    total_calories, total_protein, total_carb, total_fat,
    is_active
) VALUES (
    'plan_test_001', 1, '테스트 식단', '테스트용 식단입니다',
    1650.5, 2558.3, 2058.3, 'loss',
    2050.0, 120.0, 250.0, 60.0,
    TRUE
);

-- DietPlanMeal 삽입
INSERT INTO DietPlanMeal (
    diet_plan_id, meal_type, meal_name,
    food_description, ingredients,
    calories, protein, carb, fat,
    consumed
) VALUES (
    'plan_test_001', 'breakfast', '테스트 식단 - 아침',
    '현미밥 1공기 + 닭가슴살 구이 100g',
    JSON_ARRAY('현미밥 1공기', '닭가슴살 구이 100g'),
    450.0, 35.0, 55.0, 8.0,
    FALSE
);

-- 조회 확인
SELECT * FROM v_diet_plan_summary WHERE diet_plan_id = 'plan_test_001';

-- 테스트 데이터 삭제
DELETE FROM DietPlanMeal WHERE diet_plan_id = 'plan_test_001';
DELETE FROM DietPlan WHERE diet_plan_id = 'plan_test_001';
```

---

## 📝 주의사항

1. **운영 DB 마이그레이션 시 주의**
   - 피크 시간대를 피해 실행
   - 백업 필수
   - 롤백 계획 수립

2. **외래키 제약조건**
   - User 테이블의 user_id가 존재해야 함
   - UserFoodHistory의 history_id와 연결 가능

3. **JSON 타입**
   - MySQL 5.7 이상 필요
   - ingredients 컬럼은 JSON 배열로 저장

4. **인덱스**
   - 자주 조회되는 컬럼에 인덱스 추가됨
   - user_id, created_at, is_active 등

---

## 🎯 다음 단계

마이그레이션 완료 후:

1. ✅ 백엔드 서버 재시작
2. ✅ 프론트엔드에서 식단 저장 테스트
3. ✅ DB에서 데이터 확인
4. ✅ 조회 API 추가 (선택사항)

---

## 📞 문제 발생 시

에러가 발생하면:

1. 에러 메시지 확인
2. 외래키 참조 테이블 확인 (User, UserFoodHistory)
3. MySQL 버전 확인 (5.7 이상)
4. 권한 확인 (CREATE TABLE 권한 필요)

도움이 필요하면 백엔드 로그를 확인하세요! 🚀


