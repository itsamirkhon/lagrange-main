# ONTOLOGY.md — онтология LAGRANGE Rx

**Статус:** спецификация для разработки, 17.11.2026.
**Связанные документы:** `docs/PRICING_3735.md` (правила цен), `docs/CONTRACT_V2.md` (формат API), `docs/SIMULATION.md` (симулированный бизнес — внешний «клиент»).
**Опирается на код:** ветка `feat/twin-compliance-core` (`schemas/contracts.py`, `rules/uz_pharma_rules.yaml`, `twin/engine.py`, `compliance/checker.py`).

---

## 1. Что это и зачем

Онтология — единая модель мира продукта:
- какие **объекты** есть в бизнесе фармимпортёра;
- как они **связаны**;
- каким **правилам** (аксиомам) подчиняются.

Из неё строится всё остальное:

```
документы клиента ──Ingest──▶ экземпляры онтологии ──▶ цифровой двойник ──▶ решатель (MILP)
                                     ▲                          │
                              правила (аксиомы)          проверка (compliance)
```

Ключевые требования:
1. **Одна онтология на всех клиентов.** Новый импортёр = новые экземпляры, а не новая схема.
2. **У каждого значения есть источник** (Provenance) — без исключений.
3. **Правила — это данные, а не код.** Аксиомы лежат в YAML с версией и ссылкой на норму. Код их исполняет.
4. **Онтология не знает про симуляцию.** Симуляция — один из клиентов. Продукт видит только её документы.

Реализация: типизированные Pydantic v2 модели + YAML правил. **Без OWL/RDF** — на хакатоне это лишняя инфраструктура. Экспорт в граф (JSON-LD / Mermaid) делается для презентации.

---

## 2. Слои онтологии

| Слой | Что описывает | Объекты |
|---|---|---|
| **L-REG** Регулирование | закон и реестр | `Regulation`, `Rule`, `RegistryRecord`, `PriceIndexationEvent` |
| **L-PROD** Продукт | препарат | `Product` (SKU), `Manufacturer`, `RegistrationCertificate` |
| **L-SUP** Поставка | откуда и как приходит товар | `Supplier`, `ProductionSite`, `Carrier`, `Route`, `Quote`, `Contract`, `Shipment`, `Lot`, `CustomsEntry` |
| **L-FIN** Деньги | стоимость, курс, финансирование | `Currency`, `FxRate`, `CostItem`, `CreditLine`, `PaymentTerms` |
| **L-DEM** Сбыт | кому и сколько продаём | `Warehouse`, `Region`, `Channel`, `Customer`, `SalesRecord`, `DemandForecast` |
| **L-DEC** Решения | что считает система | `Scenario`, `Plan`, `PlanLine`, `Constraint`, `CheckResult`, `Snapshot` |
| **L-TRUST** Доверие | откуда данные | `Provenance`, `SourceDocument`, `Evidence`, `DataQualityIssue` |

---

## 3. Объекты и атрибуты

Обозначения: `?` — необязательное поле, `→X` — ссылка на объект X. Все деньги без НДС, если не указано `_incl_vat`. Валюта денег — UZS, если не указано иное.

### 3.1 L-TRUST — доверие (используется всеми)

**Provenance** — прикреплено к каждому объекту, а при необходимости к отдельному полю.
- `kind`: `official | user_provided | extracted_confirmed | extracted_unconfirmed | synthetic | assumption`
- `source`: строка (URL нормы, имя документа + страница, имя файла)
- `source_document?` → SourceDocument
- `retrieved_at?`, `confirmed_by?`, `confirmed_at?`, `note?`

**SourceDocument** — файл, полученный от клиента.
- `document_id` (sha256 содержимого), `file_name`, `mime`, `pages`, `uploaded_at`
- `doc_class?`: `sales_export | regional_report | supplier_quote | carrier_quote | contract | invoice | customs | waybill | stock | payments | memo | email | registry_extract | unknown`
- `language?`: список из `ru | uz-latn | uz-cyrl | en`

**Evidence** — где в документе найдено значение.
- → SourceDocument, `page`, `bbox?`, `text_span`, `confidence` 0..1

**DataQualityIssue** — найденная проблема в данных.
- `issue_type`: `contradiction | missing | duplicate | currency_ambiguous | vat_ambiguous | unit_ambiguous | stale | arithmetic | unreadable | irrelevant`
- `severity`: `blocking | warning | info`
- `affected`: список ссылок (объект + поле)
- `evidence`: список Evidence
- `resolution?`: `confirmed_value | user_override | ignored | unresolved`

> Эти типы проблем специально совпадают с операторами порчи симуляции. Тогда оценка «нашла ли система ошибку» сводится к простому сопоставлению.

### 3.2 L-REG — регулирование

**Regulation** — нормативный акт.
- `regulation_id` (`UZ-PF13`, `UZ-PKM185`, `UZ-MOH-3735`), `title`, `number`, `registered_on`, `effective_from`, `effective_to?`, `url` (lex.uz), `replaces?` → Regulation

**Rule** — аксиома, исполняемая двойником и проверяльщиком (раздел 5).
- `rule_id`, → Regulation, `point` (пункт, например «п.19»), `kind`: `ceiling | markup | formula | indexation | revision | invariant | trust`
- `parameters` (словарь), `effective_from`
- `status`: `verified | needs_legal_review | modeling_assumption`
- `description`

**RegistryRecord** — запись реестра референтных цен для SKU.
- → Product, `registration_number`, `registry_date`
- `rn_fx`, `rn_currency`, `filing_fx_rate`
- `customs_b_uzs`, `bx_share` (= 0,02), `vat_rate` (= 0,12)
- `chun_incl_vat` и `chchn_incl_vat` — как опубликовано
- `chun_formula_incl_vat` — вычисляется по п.19, не вводится вручную
- `rn_candidates?`: { `ref_countries_avg`, `origin_plus_freight`, `cip_12m_avg` }
- → Provenance

**PriceIndexationEvent** — изменение потолка.
- → RegistryRecord, `event_type`: `registration | index_p20_scheduled | index_p20_immediate | revision_p22 | official_cut`
- `effective_date`, `old_chun`, `new_chun`, `fx_ratio?`, `reason`

### 3.3 L-PROD — продукт

**Product (SKU)** — `sku_id`, `trade_name`, `inn`, `dosage_form`, `strength`, `pack_size`, `rx: true`, → Manufacturer, → RegistrationCertificate, `is_generic?`, `originator_sku?` → Product

**Manufacturer** — `name`, `country`, `same_composition_skus?` → [Product] (нужно для правила п.12 «одинаковый состав — минимальная цена»)

**RegistrationCertificate** — `number`, `valid_from`, `valid_to`, `holder`

### 3.4 L-SUP — поставка

**Supplier** — `supplier_id`, `name`, `country`, `payment_terms` → PaymentTerms, `reliability?` (вероятность срыва)

**ProductionSite** — → Supplier, `country`, `approved_for` → [Product]

**Carrier** — `carrier_id`, `name`, `modes`

**Route** — путь товара. Это то, что решатель выбирает.
- `route_id`, → ProductionSite, → Carrier
- `transport_mode`: `air | road | rail | multimodal`
- `incoterms`, `customs_post`
- `lead_time_days` (распределение: `p50`, `p90`)
- `freight_per_shipment_uzs`, `freight_per_pack_uzs`
- `min_shipment_packs`, `max_shipment_packs`, `max_shipments_per_year`, `capacity_packs_per_year`
- `activation_cost_uzs`, `is_eligible` (результат фильтра допустимости)

**Quote** — коммерческое предложение (источник цен и условий).
- `quote_id`, → Supplier или Carrier, → Product, `issued_on`, `valid_until`
- `unit_price`, `currency`, `incoterms`, `moq_packs`, `lead_time_days`, `min_remaining_shelf_life_months`, `vat_status`: `incl | excl | unknown`
- → Provenance (обычно `extracted_*`), Evidence по каждому полю

**Contract** — `contract_id`, → Supplier, `version`, `signed_on`, `valid_from`, `valid_to`, `unit_price`, `currency`, `moq_packs`, `payment_terms`, `supersedes?` → Contract

**Shipment** — `shipment_id`, → Route, `dispatch_date`, `arrival_date`, `packs`, `actual_freight_uzs`, → [Lot], → CustomsEntry

**Lot** — `lot_number`, → Product, `production_date`, `expiry_date`, `qty_packs`, → Shipment, `remaining_packs`

**CustomsEntry** (учёт растаможки) — `entry_ref`, `entry_date`, `cip_value_fx`, `currency`, `fx_rate_on_entry` (курс ЦБ на дату ГТД), `duty_uzs`, `fees_uzs`, `vat_uzs`
- Вычисляемое поле `legal_base_c_per_pack` = (Kc по курсу ЦБ на дату ГТД + T + Pr) / packs — база для правила 15%.

### 3.5 L-FIN — деньги

**Currency** — ISO-код.

**FxRate** — `currency`, `date`, `rate_uzs` (за 1 единицу), `source` (официальный API ЦБ).

**CostItem** — затрата.
- `category`: `freight | customs | certification | other_acquisition | distribution | marketing | warehousing | financing | write_off | g_and_a`
- `layer`: `acquisition` (входит в законную базу C) или `below_line` (не входит)
- `amount_uzs`, `period`, `driver`: `per_pack | per_shipment | fixed | per_uzs_financed`
- → Shipment или период

**CreditLine** — `limit_uzs`, `annual_rate`, `available_from`

**PaymentTerms** — `supplier_days`, `customer_days`, `prepayment_share`

**FxHedge** (опционально) — `currency`, `share`, `forward_rate`, `tenor_days`. Влияет только на экономическую себестоимость, **не** на законную базу (правило `UZ-INV-LEGAL-VS-ECONOMIC-BASE`).

### 3.6 L-DEM — сбыт

- **Warehouse** — `warehouse_id`, `region`, `capacity_packs`, `is_commingled` (партии смешаны)
- **Region** — 14 регионов РУз (`REG_*`)
- **Channel** — `distributors | pharmacy_chains | hospital_tenders`
- **Customer** — `name`, → Channel, → Region
- **SalesRecord** — `period`, → Product, → Region, → Channel, `qty_packs`, `price_ex_vat`, → Lot?
- **DemandForecast** — `period`, → Product, `p10`, `p50`, `p90`, `method`, `coverage_calibrated: bool`

### 3.7 L-DEC — решения

**Scenario**
- `scenario_id`, `probability`
- `fx_shock` (по валютам)
- `freight_multiplier`
- `supplier_disrupted` → [Route]
- `demand_multiplier`
- `cap_rule`: `index_p20 | fixed | official_revision` (по умолчанию для импорта — `index_p20`)
- `index_lag_days`, `official_revision_pct`

**Plan** — результат решателя.
- `plan_id`, → [PlanLine], `wholesale_price_ex_vat`, `objective`, `risk_mode`: `expected | cvar | worst_case`, `lambda`, `alpha?`
- `solver_status`, `gap`, → Snapshot

**PlanLine** — → Route, `packs`, `shipments`, `period?`

**Constraint** — `name`, `type`: `legal | operational | financial`, → Rule?, `is_binding`, `slack`, `scenario_id`

**CheckResult** — → Rule, `passed`, `observed`, `limit`, `message`

**Snapshot** — `snapshot_id` (sha256 от входов, сценариев, версии правил, версии движка и коммита кода), `created_at`

---

## 4. Ключевые связи

```
Product ─has─▶ RegistryRecord ─governed_by─▶ Rule ─part_of─▶ Regulation
Product ─made_at─▶ ProductionSite ─of─▶ Supplier
Quote ─offers─▶ Product ─via─▶ Route ─uses─▶ Carrier
Contract ─supersedes─▶ Contract
Shipment ─on─▶ Route ─brings─▶ Lot ─cleared_by─▶ CustomsEntry
CostItem ─attached_to─▶ Shipment | Period
SalesRecord ─sells─▶ Lot? ─in─▶ Region/Channel
Plan ─allocates─▶ PlanLine ─on─▶ Route
Constraint ─derived_from─▶ Rule | Route | CreditLine
CheckResult ─verifies─▶ Rule
* ─has─▶ Provenance ─cites─▶ SourceDocument/Evidence
DataQualityIssue ─about─▶ (object, field)
```

---

## 5. Правила (аксиомы)

Все правила лежат в `rules/uz_pharma_rules.yaml`. Ниже — целевой список. `status` обязателен: **verified** (проверено по тексту нормы), **needs_legal_review** (нужна проверка юристом или бухгалтером), **modeling_assumption** (наше допущение, а не закон).

| rule_id | Формула / смысл | Норма | status |
|---|---|---|---|
| `UZ-3735-P19-WHOLESALE-CEILING` | цена опта (с НДС) ≤ ChUN | Приказ №3735, п.19 | verified* |
| `UZ-3735-P19-RETAIL-CEILING` | цена розницы (с НДС) ≤ ChChN = ChUN × 1,20 | №3735, п.19 | verified* |
| `UZ-3735-P19-CEILING-FORMULA` | ChUN = (Rn + B + 0,02·Rn) × 1,12 × 1,15; отклонение от опубликованного > 1% → DataQualityIssue | №3735, п.19 | verified* |
| `UZ-3735-P11-RN-MIN` | Rn = минимум из кандидатов (средняя по 10 странам; цена в стране происхождения + ≤15% страховка и фрахт; средняя CIP за 12 мес.) | №3735, п.11 | needs_legal_review (из каких именно кандидатов берётся минимум) |
| `UZ-3735-P20-INDEXATION` | рост курса ≥3% → индексация 1 января или 1 июля; >5% → сразу | №3735, п.20 | verified* |
| `UZ-3735-P22-REVISION` | падение цены ≥15% → перерегистрация за 10 рабочих дней; розница меняется через 3 месяца | №3735, п.22 | verified* |
| `UZ-3735-P12-SAME-COMPOSITION` | один производитель, одинаковый состав → минимальная Rn на всех; дженерик ≤ 80% оригинала | №3735, п.12 | verified* |
| `UZ-PF13-WHOLESALE-MARKUP` | цена опта ≤ 1,15 × законная база C, независимо от числа посредников | УП-13; ПКМ №185 | needs_legal_review (проверяется ли от фактической базы в ЭСФ) |
| `UZ-PF13-RETAIL-MARKUP` | цена розницы ≤ 1,20 × цена закупки аптекой | УП-13 | verified |
| `UZ-PKM185-LEGAL-BASE` | C = Kc (по курсу ЦБ на дату ГТД) + T + Pr, без возмещаемого НДС | ПКМ №185 и разъяснения | needs_legal_review |
| `UZ-VAT-NORMALIZATION` | потолки реестра с НДС 12%; P&L и проверки наценки — без НДС | №3735, п.19–20 | verified* |
| `UZ-INV-LEGAL-VS-ECONOMIC-BASE` | хедж и прочие финансовые инструменты меняют экономическую себестоимость, но **не** законную базу C | — | modeling_assumption |
| `UZ-INV-UNIFORM-WAREHOUSE-PRICE` | смешанные партии без раздельного учёта продаются по одной цене = min по партиям | — | **modeling_assumption** (не закон!) |
| `UZ-INV-CIP-RATCHET` | снижение собственной CIP может снизить будущий Rn через `cip_12m_avg` | №3735, п.11, п.22 | modeling_assumption (зависит от п.11) |
| `TRUST-NO-UNCONFIRMED` | значение с `extracted_unconfirmed` не входит в расчёт | — | invariant |
| `TRUST-VALID-REGISTRATION` | у SKU есть действующее регистрационное удостоверение | — | invariant |
| `TRUST-FX-OFFICIAL` | курс для законной базы — только официальный курс ЦБ на дату | №3735, п.11 | verified* |

\* «verified» здесь означает «проверено по машинному переводу узбекского текста». До финала нужна сверка носителем языка (см. `PRICING_3735.md`, раздел 5).

**Исправления относительно текущей ветки:**
1. `UZ-PKM185-BASE-COST` сейчас цитирует №3735 → сослаться на ПКМ №185, статус `needs_legal_review`.
2. `UZ-INV-UNIFORM-WAREHOUSE-PRICE` сейчас цитирует №3735 п.12 → п.12 про другое; статус `modeling_assumption`.
3. Добавить `UZ-INV-LEGAL-VS-ECONOMIC-BASE`. Сейчас хедж ошибочно меняет законную базу и в двойнике, и в проверяльщике.
4. `cap_rule` по умолчанию для импортных препаратов — `index_p20`, с разделением 3% / 5% и `index_lag_days`.
5. Добавить `UZ-3735-P19-CEILING-FORMULA` как сверку опубликованных потолков с формулой.

---

## 6. Как документы ложатся в онтологию

Это и есть задача Ingest. Документ → классификация (DecisionScorer, раздел 10) → извлечение полей с Evidence (генеративный Extractor) → нормализация (код) → экземпляры онтологии в статусе `extracted_unconfirmed` → проверки качества (код + DecisionScorer) → подтверждение человеком → `extracted_confirmed`.

| Класс документа | Создаёт / обновляет | Ключевые поля | Типичные проблемы |
|---|---|---|---|
| Выгрузка продаж (1С) | SalesRecord | период, регион, канал, кол-во, цена | форматы чисел и дат, пропуски |
| Отчёт региона (XLSX) | SalesRecord | то же | объединённые ячейки, разные шаблоны, дубли с 1С |
| КП поставщика | Quote | цена, валюта, Incoterms, MOQ, срок, годность | валюта не указана, НДС, устаревание |
| КП перевозчика / чат | Quote, Route | тариф, срок, MOQ | обрезанный скриншот, единицы |
| Контракт | Contract | цена, валюта, MOQ, условия оплаты, срок действия | несколько версий, противоречие с КП |
| Инвойс / упаковочный лист | Shipment, Lot | даты, кол-во, партии, годность | единицы (упаковка / блистер / коробка) |
| Учёт растаможки | CustomsEntry, CostItem | CIP, курс, пошлина, сборы | курс не на ту дату, валюта |
| Накладная склада | Shipment, Lot | кол-во, партия | фото, печать поверх цифр |
| Остатки | Lot | остаток, срок годности | пропуски |
| Платежи и кредит | CreditLine, CostItem(financing) | лимит, ставка, проценты | арифметика |
| Записка / письмо | любые (как подсказка) | — | рукопись, неформальный текст |
| Выписка реестра | RegistryRecord | Rn, B, ChUN, ChChN, дата | устаревшая выписка |

**Сопоставление сущностей (entity resolution):** документы не содержат общих ключей. Связь строится по признакам:
- торговое название с нормализацией латиницы и кириллицы;
- номер партии, номер инвойса;
- даты и количества;
- контрагент.

Каждая связь хранит свою уверенность и Evidence.

---

## 7. Как онтология превращается в двойник и решатель

**Двойник** — детерминированная функция онтологии:
- законная база C = из CustomsEntry и CostItem(layer = acquisition), курс — из FxRate (официальный ЦБ);
- экономическая себестоимость = законная база ± хедж + CostItem(layer = below_line);
- потолки — из RegistryRecord + правила п.19, п.20;
- цена = min(потолки, 1,15 × C, контракт), с указанием связывающего ограничения;
- **калибровочная проверка:** двойник по документам клиента должен воспроизвести его фактическую маржу. Расхождение → DataQualityIssue «не хватает данных о…».

**Решатель** строится из онтологии автоматически:

| Онтология | Решатель |
|---|---|
| Route (`is_eligible = true`) | переменные `x_i` (упаковки), `n_i` (отгрузки), `y_i` (активация) |
| Route: min/max/capacity/max_shipments | ограничения партий и мощности |
| CreditLine, PaymentTerms | ограничение оборотного капитала |
| RegistryRecord + Rules (`ceiling`, `markup`) | ограничения цены, в каждом сценарии |
| Scenario | коэффициенты по сценариям (курс → база, фрахт, сбой → `U_i = 0`, индексация потолка) |
| DemandForecast | спрос (p50 или сценарии) |
| CostItem | коэффициенты целевой функции |
| Plan.risk_mode | целевая: среднее / CVaR / худший случай — три разных режима, явно подписанных |

Результат решения пишется обратно в онтологию как Plan, PlanLine, Constraint (`is_binding`), CheckResult и Snapshot. «Что если» считается **повторным решением**; двойственные цены MILP не используются.

---

## 8. Реализация

```
src/lagrange_rx/ontology/
  trust.py        # Provenance, SourceDocument, Evidence, DataQualityIssue
  regulation.py   # Regulation, Rule, RegistryRecord, PriceIndexationEvent
  product.py      # Product, Manufacturer, RegistrationCertificate
  supply.py       # Supplier, ProductionSite, Carrier, Route, Quote, Contract, Shipment, Lot, CustomsEntry
  finance.py      # FxRate, CostItem, CreditLine, PaymentTerms, FxHedge
  demand.py       # Warehouse, Region, Channel, Customer, SalesRecord, DemandForecast
  decision.py     # Scenario, Plan, PlanLine, Constraint, CheckResult, Snapshot
  graph.py        # ClientGraph: контейнер всех экземпляров одного клиента + индексы + валидация ссылок
  export.py       # JSON-LD / Mermaid для визуализации
rules/uz_pharma_rules.yaml   # аксиомы (раздел 5)
```

**Миграция с текущего кода:**
- `schemas/contracts.py` (ветка) → переносится в `ontology/*`. Имена полей сохраняются там, где они уже есть (`option_id` → `route_id` с алиасом).
- `models.py` (`main`, старый решатель) → тонкий адаптер `ontology → SolverInput`. Два разных `SupplyOption` с одинаковым именем больше не существуют.
- `ClientGraph` — единственный вход для двойника, решателя и проверяльщика.

**Проверяльщик (compliance)** должен быть действительно независимым. Он считает потолки и базу **своей** реализацией формул из YAML, а не копией кода двойника. Отдельный тест сверяет обе реализации на 10 000 случайных входов.

---

## 9. Definition of done

- [ ] Все объекты раздела 3 реализованы как Pydantic-модели с `extra = "forbid"`, у каждого есть Provenance.
- [ ] `ClientGraph` проверяет ссылочную целостность (нет «висячих» ссылок) и запрещает `extracted_unconfirmed` на входе в двойник.
- [ ] YAML содержит все правила раздела 5 со статусами, а ссылки на нормы исправлены (п.1–5 в разделе 5).
- [ ] Двойник считает законную базу и экономическую себестоимость раздельно; хедж не меняет законную базу (тест).
- [ ] Индексация п.20 реализована с порогами 3% / 5% и задержкой (тесты на 2,9%, 3,0%, 5,0%, 5,1%).
- [ ] Формула п.19 сверяет опубликованные потолки (тест на расхождение > 1%).
- [ ] Адаптер `ClientGraph → решатель` работает, старые тесты решателя (перебор, оракул) зелёные.
- [ ] Двойник, собранный по документам мира L0 симуляции, воспроизводит маржу 2–3% из её истины (сквозной тест через модуль оценки, без импорта истины в продукт).
- [ ] Экспорт графа в Mermaid для слайда.
- [ ] `DecisionScorer` с тремя реализациями (Gemini, Replay, Heuristic), общий набор тестов; банк вопросов в YAML (раздел 10.3).
- [ ] Ответы DecisionScorer пишутся в онтологию с полным распределением и Evidence; пороги 10.5 применяются, денежные поля на L2+ не подтверждаются автоматически (тест).
- [ ] Отчёт оценки DecisionScorer по мирам L0–L4: точность, ECE, recall по операторам порчи, доля ручных подтверждений, сравнение с HeuristicScorer.
- [ ] Записан полный прогон ответов для `ReplayScorer`; демо работает без сети.

---

## 10. DecisionScorer — закрытые решения (Gemini Decision 1)

### 10.1 Роль

В конвейере Ingest разделены **генерация** и **выбор**:

| Задача | Исполнитель |
|---|---|
| Извлечь значение (цену, MOQ, дату, количество) | генеративная модель со структурным выводом (`Extractor`) |
| Выбрать из закрытого набора вариантов (класс документа, статус НДС, к какой отгрузке относится, какая версия контракта действует, нужна ли проверка) | **`DecisionScorer`** — по умолчанию Gemini Decision 1 |
| Посчитать, применить закон, оптимизировать | детерминированный код |

Gemini Decision 1 (Google, Decisions API, превью с 11.11.2026):
- оценивает **заранее заданные варианты** за один проход и возвращает вероятности;
- принимает один общий вход (текст, изображения, PDF) и до 1 000 вопросов на запрос;
- **не генерирует текст**, поэтому значения не извлекает.

Именно поэтому он подходит на роль выбора: ИИ не придумывает, а выбирает из списка и сообщает уверенность.

**Принцип:** любой ответ DecisionScorer — это **предложение с вероятностью**, а не факт. Он попадает в онтологию со статусом `extracted_unconfirmed`, пока не пройдёт пороги из 10.5 или подтверждение человеком.

### 10.2 Интерфейс (модель заменяема)

```python
class DecisionQuestion(BaseModel):
    qid: str                                   # стабильный id вопроса, например "D3.vat_status"
    kind: Literal["boolean", "choice", "score"]
    instructions: str                          # формулировка вопроса (ru/en)
    options: list[str] | None = None           # для choice; всегда включает "unknown" / "none_of_these"
    rubric: list[str] | None = None            # для score: упорядоченные уровни 0..k

class DecisionAnswer(BaseModel):
    qid: str
    distribution: dict[str, float]             # вероятности по вариантам, сумма = 1
    top: str
    confidence: float                          # = вероятность top
    model_id: str                              # например "gemini-decision-1"
    latency_ms: int

class DecisionScorer(Protocol):
    def decide(self, document: SourceDocument, questions: list[DecisionQuestion]) -> list[DecisionAnswer]: ...
```

**ИИ-провайдер — OpenRouter, везде.** Все вызовы моделей в проекте (Extractor, DecisionScorer, эмбеддинги) идут через единый клиент `ai/openrouter.py`. Модели задаются в `config/ai.yaml`, ключ — только из `OPENROUTER_API_KEY`.

Реализации:
1. `OpenRouterDecisionScorer` — основная. Модель `google/gemini-decision-1` через OpenRouter. **Проверить до старта:** у Decision 1 отдельный Decisions API, а не обычные chat completions, поэтому через OpenRouter она может быть недоступна или вызываться иначе. Если недоступна — **план Б, тоже через OpenRouter:** генеративная модель в режиме классификации. Строгий JSON с выбором из `options` + распределение вероятностей из logprobs, если модель их отдаёт, иначе самооценка уверенности. Интерфейс 10.2 не меняется, и калибровка (10.6) обязательна для любого варианта.
2. `ReplayScorer` — читает заранее записанные ответы из `cache/decisions/{document_id}.json`. Нужен для демо без сети и для детерминированных тестов.
3. `HeuristicScorer` — простые правила (регулярные выражения, ключевые слова). Нужен как запасной вариант и как базовая линия для оценки.

Все реализации проходят один набор тестов. Замена модели не меняет остальной код.

### 10.3 Банк вопросов

Вопросы хранятся в `ingest/questions/*.yaml`, не в коде. У каждого есть `qid`, версия и связь с объектом и полем онтологии. Каждый вариант `choice` включает `unknown` — модель не должна быть вынуждена угадывать.

**Q0 — для любого документа (первый проход):**

| qid | Тип | Варианты | Куда пишется |
|---|---|---|---|
| `ALL.doc_class` | choice | `sales_export, regional_report, supplier_quote, carrier_quote, contract, invoice, customs, waybill, stock, payments, memo, email, registry_extract, irrelevant, unknown` | SourceDocument.doc_class |
| `ALL.language` | choice (мультиметка через boolean) | `ru, uz-latn, uz-cyrl, en` | SourceDocument.language |
| `ALL.about_focus_sku` | choice | `yes, no, mentions_several, unknown` | фильтр релевантности |
| `ALL.readability` | score | `0 нечитаемо … 3 полностью читаемо` | DataQualityIssue(`unreadable`) |
| `ALL.is_photo_or_scan` | boolean | — | маршрутизация к OCR |
| `ALL.has_handwritten_edits` | boolean | — | DataQualityIssue (R3) |
| `ALL.stamp_over_numbers` | boolean | — | DataQualityIssue (R2) |

**Q1 — по классу документа (второй проход, после извлечения значений):**

| Класс | qid | Тип | Варианты |
|---|---|---|---|
| supplier_quote / carrier_quote | `Q.currency_stated` | choice | `explicit, implied_by_context, absent` |
| | `Q.price_currency` | choice | `USD, EUR, RUB, UZS, unknown` |
| | `Q.vat_status` | choice | `incl_vat, excl_vat, not_stated` |
| | `Q.price_unit` | choice | `per_pack, per_blister, per_box_of_n, per_shipment, unknown` |
| | `Q.is_expired_on(date)` | boolean | — |
| | `Q.incoterms` | choice | `CIP, CPT, DAP, FCA, EXW, other, unknown` |
| contract | `C.is_latest_version` (если версий несколько) | choice | `v1, v2, …, cannot_tell` |
| | `C.conflicts_with_quote(q_id)` | choice | `no_conflict, moq_differs, price_differs, terms_differ, unknown` |
| invoice / waybill | `W.qty_unit` | choice | `packs, blisters, boxes, unknown` |
| | `W.is_duplicate_of(candidates)` | choice | `<doc_id_1>, <doc_id_2>, …, not_duplicate` |
| customs | `K.fx_date_basis` | choice | `customs_entry_date, contract_date, invoice_date, unknown` |
| | `K.amount_currency` | choice | `UZS, USD, EUR, mixed, unknown` |
| sales_export / regional_report | `S.months_covered_complete` | boolean | — |
| | `S.overlaps_with(doc_id)` | choice | `same_data, partial_overlap, disjoint, unknown` |
| payments | `P.totals_consistent` | boolean | дополняет арифметическую проверку кодом, но не заменяет её |
| registry_extract | `R.is_current_on(date)` | choice | `current, outdated, unknown` |

**Q2 — связи (entity resolution).** Код сначала строит **кандидатов**: до 10 лучших по точным и нечётким признакам — номер партии, дата ±7 дней, количество ±5%, нормализованное название. Только потом DecisionScorer выбирает:

| qid | Вопрос | Варианты |
|---|---|---|
| `E.waybill_to_shipment` | К какой отгрузке относится накладная? | кандидаты + `none_of_these` |
| `E.invoice_to_contract` | По какому контракту выставлен инвойс? | кандидаты + `none_of_these` |
| `E.quote_to_route` | Какому маршруту соответствует КП перевозчика? | кандидаты + `new_route` + `none_of_these` |
| `E.lot_to_shipment` | В какой отгрузке пришла партия? | кандидаты + `none_of_these` |
| `E.trade_name_match` | Это тот же препарат? | `same_sku, same_inn_other_sku, different, unknown` |

Модель **никогда** не выбирает из открытого множества. Если правильного кандидата нет, правильный ответ — `none_of_these`, и это нормальный исход, а не ошибка.

### 10.4 Как ответ попадает в онтологию

```
DecisionAnswer ─▶ поле/связь онтологии (status = extracted_unconfirmed)
               ─▶ Evidence { model_id, qid, distribution, confidence, document_id }
               ─▶ при необходимости DataQualityIssue (например vat_status = not_stated → vat_ambiguous)
```

Распределение вероятностей хранится целиком, а не только лучший вариант. Это нужно для аудита и калибровки.

### 10.5 Пороги и человек в контуре

| Условие | Статус | Действие |
|---|---|---|
| confidence ≥ τ_auto **и** вопрос не влияет на деньги **и** код не нашёл противоречий | `extracted_confirmed` (auto) | принимается автоматически, видно в аудите |
| τ_review ≤ confidence < τ_auto, **или** вопрос влияет на деньги (валюта, НДС, единицы, версия контракта, связь с отгрузкой) | `extracted_unconfirmed` | в очередь подтверждения человеку: варианты с вероятностями + цитата |
| confidence < τ_review, **или** top = `unknown` / `none_of_these` | `extracted_unconfirmed` + DataQualityIssue | вопрос человеку или запрос недостающего документа |

Стартовые значения: τ_auto = 0,95, τ_review = 0,60. Финальные пороги подбираются **на симуляции** (10.6), а не на глаз.

**Жёсткое правило:** всё, что меняет деньги — валюта, НДС, единицы, цена, версия контракта, — **не подтверждается автоматически** ни при какой уверенности на уровнях L2+. Решает человек, а система только ускоряет.

### 10.6 Оценка на симуляции

Симуляция знает истину и все заложенные ошибки (манифест), поэтому DecisionScorer измеряется честно, по уровням L0–L4:

1. **Точность** по каждому qid: доля верных `top`.
2. **Калибровка:** надёжна ли уверенность. Из ответов с уверенностью около 0,9 верны ли около 90%? Метрики: Expected Calibration Error (ECE) и диаграмма надёжности. Если калибровка плохая, применяется калибровка по температуре на L1, проверка — на L2–L4.
3. **Обнаружение порч:** recall и precision по операторам из манифеста (C1, V1, U1, X2, D1x, R2, R3, J1…).
4. **Нагрузка на человека:** доля вопросов, ушедших на подтверждение, при заданной итоговой точности.
5. **Сравнение реализаций** на одних и тех же документах: Gemini Decision 1 против `HeuristicScorer`, и при желании против генеративной модели в режиме классификации. Модель остаётся в продукте, только если выигрывает на этих метриках.
6. **Задержка и стоимость** на документ — для слайда о масштабировании.

### 10.7 Надёжность и данные

- **Кеш:** ответы кешируются по `(document_id, qid, question_version, model_id)`. Повторный запуск детерминирован.
- **Нет сети или API** → `ReplayScorer`. Для демо записывается полный прогон по всем мирам L0–L4.
- **Модель в превью** → закрепить `model_id` с датой версии и записать его в Snapshot.
- **Данные клиента:** на хакатоне — только синтетика. Для реальных клиентов — явное согласие на обработку внешним API (OpenRouter передаёт данные выбранному провайдеру модели) и возможность переключиться на модель внутри контура клиента. Для этого и нужен интерфейс 10.2.

---

## 11. Чего онтология НЕ делает

- Не извлекает данные из документов — это Ingest; онтология задаёт только, **куда** их класть.
- Не решает оптимизацию — это решатель; онтология задаёт, **из чего** он строится.
- Не хранит истину симуляции — продукт её никогда не видит.
