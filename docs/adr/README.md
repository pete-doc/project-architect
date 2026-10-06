# Решения (ADR)

> Индекс собирается автоматически командой `/parch:adr`, вручную его не правят.
> Статус `proposed` значит «ждёт утверждения владельца»,
> статус `accepted` ставит только владелец.

| № | Решение | Статус | Тип | Дата |
|---|---|---|---|---|
| [0001](0001-form-product-plugin-templates.md) | Форма продукта: плагин + шаблоны | accepted | необратимое | 2026-10-02 |
| [0002](0002-hooks-python-cross-platform.md) | Hooks и скрипты на Python ради macOS и Windows | accepted | необратимое | 2026-10-02 |
| [0003](0003-agent-merges-pull-requests.md) | Слияние pull request выполняет агент по строгим условиям | accepted | обратимое | — |
| [0004](0004-hooks-fail-closed-roles-permissions.md) | Как устроены hooks: границы действия, поведение при сбое, роли, снятие блокировки и permissions | accepted | необратимое | 2026-10-02 |
| [0005](0005-zapuskayushchiy-fayl-hooks.md) | Запускающий файл hooks сам находит Python | accepted | необратимое | 2026-10-02 |
| [0006](0006-shablony-vnutri-plagina-i-navyki.md) | Шаблоны лежат внутри плагина; устройство навыков init-project, adr и doctor | accepted | необратимое | 2026-10-02 |
| [0007](0007-ci-proverki-i-hrapovik.md) | CI-проверки по языкам: общий скрипт, «храповик» по отпечаткам, честные «красные» | accepted | необратимое | 2026-10-02 |
| [0008](0008-ci-typescript.md) | CI для TypeScript: набор инструментов и правила против «заглушек» | accepted | необратимое | 2026-10-02 |
| [0009](0009-ci-csharp.md) | CI для C#: набор инструментов, выбор ArchUnitNET и защита от «пустых» правил архитектуры | accepted | необратимое | 2026-10-02 |
| [0010](0010-ci-powershell.md) | PowerShell только тонкий «клей»: облегчённый CI вместо полного набора проверок | accepted | необратимое | 2026-10-03 |
| [0011](0011-ci-ubuntu-po-umolchaniyu.md) | CI продукта и шаблонов: Ubuntu по умолчанию; матрицы, не-Linux раннеры и запуск не по PR только через ADR с оценкой квоты | accepted | обратимое | 2026-10-03 |
| [0012](0012-tablo-status-na-vetke.md) | Табло STATUS.md: пересчёт после слияния и где оно публикуется | accepted | необратимое | 2026-10-03 |
| [0013](0013-razdelenie-ci.md) | Разделение CI: быстрая часть на каждую отправку, полный прогон перед слиянием | accepted | обратимое по коду | 2026-10-03 |
| [0014](0014-priemka-vladeltsem.md) | Приёмка владельцем для блоков, которые не проверяются тестами | accepted | обратимое | 2026-10-03 |
| [0015](0015-katalog-vozmozhnostey.md) | Каталог возможностей и описания функций проверяет parch_ci, а не линтер языка | accepted | обратимое | 2026-10-04 |
| [0016](0016-isklyuchenie-v-ohrane-putei-novye-md-shablony-pod-plugin-tem.md) | Исключение в охране путей: новые .md-шаблоны под plugin/templates/ в репозитории продукта | accepted | обратимое | 2026-10-04 |
| [0017](0017-dva-parallelnyh-pr.md) | До двух параллельных PR, если они не пересекаются по файлам | accepted | обратимое | 2026-10-04 |
| [0018](0018-pravilo-biblioteka-v-odnom-module-dlya-c-typescript-i-powers.md) | Правило «библиотека в одном модуле» для C#, TypeScript и PowerShell проверяет parch_ci по тексту | accepted | обратимое | 2026-10-05 |
| [0019](0019-fail-dolga-state-standard-baseline-json-zaschischen-kak-stat.md) | Файл долга state/standard-baseline.json защищён как state/baseline.json | accepted | обратимое | 2026-10-05 |
| [0020](0020-perehod-ci-s-github-actions-na-circleci.md) | CI продукта живёт на CircleCI, GitHub Actions для CI самого репозитория убраны | accepted | обратимое | 2026-10-06 |
| [0021](0021-faily-dolga-catalog-baseline-json-i-modules-baseline-json-za.md) | Файлы долга catalog-baseline.json и modules-baseline.json защищены как standard-baseline.json | proposed | обратимое | 2026-10-06 |
