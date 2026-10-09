# Каталог возможностей

Собран автоматически из однострочных описаний публичных функций. Руками не правится: пересобрать командой `python .github/parch/parch_ci.py catalog --update`. Перед тем как писать новую функцию, найдите похожую здесь.

## Python

| Модуль | Функция | Описание |
|---|---|---|
| `plugin/hooks/guard_destructive.py` | `is_secret_path` | True, если путь похож на файл с секретами (ключи, .env, учётные данные). |
| `plugin/hooks/guard_packages.py` | `drop_redirections` | Убирает перенаправления вывода (`2>&1`, `> файл`, `2>/dev/null`): это не имена пакетов. |
| `plugin/hooks/guard_shell_writes.py` | `split_heredocs` | (команда без тел heredoc, тела heredoc). Тела не разбираются как команды. |
| `plugin/hooks/guard_shell_writes.py` | `target_allowed` | Отчёты тестов, покрытие и временные файлы вне проекта. |
| `plugin/hooks/guard_shell_writes.py` | `writes` | Что именно пишет файл проекта (None, если команда безопасна). |
| `plugin/hooks/loop_guard.py` | `branch_block` | Идентификатор блока по префиксу ветки или NONE. |
| `plugin/hooks/loop_guard.py` | `is_read_only` | Команда только читает: чтение файлов и git status/log/diff, без перенаправлений. |
| `plugin/hooks/loop_guard.py` | `loop_flag` | Запись-флаг первого обнаружения петли в этой сессии (None, если петли не было). |
| `plugin/hooks/loop_guard.py` | `report_written_since` | Есть ли отчёт в state/incidents/, записанный после флага петли. |
| `plugin/hooks/loop_guard.py` | `revert_loop` | Файл дважды вернулся к прежнему содержимому: A, B, A, B. |
| `plugin/hooks/loop_guard.py` | `session_records` | Записи журнала этой сессии по порядку (журнал: по файлу JSONL в день). |
| `plugin/hooks/loop_guard.py` | `signature_loop` | Три одинаковые подписи подряд; успешная правка («ok») прерывает ряд. |
| `plugin/hooks/post_edit_check.py` | `collect_errors` | Возвращает (строки с ошибками, были ли файл автоматически переформатирован). |
| `plugin/hooks/post_edit_check.py` | `error_signature` | Подпись ошибки (проверка, файл и текст без номеров строк): по ней loop_guard видит повтор. |
| `plugin/hooks/post_edit_check.py` | `run_step` | Запускает шаг и возвращает только строки с ошибками (пусто, если всё хорошо). |
| `plugin/hooks/pre_push.py` | `git_subcommand` | Подкоманда git: первый токен без `-`, кроме значений общих опций (`-C папка`). |
| `plugin/hooks/pre_push.py` | `is_product_repo` | Репозиторий самого продукта: в нём CONSTITUTION.md нет, но есть плагин и маркетплейс. |
| `plugin/hooks/pre_push.py` | `is_push` | `git push`, а не `git commit -m push` и не слово push в тексте команды. |
| `plugin/hooks/stop_gate.py` | `decide` | Сообщение для агента, если остановку нужно запретить; None, если можно останавливаться. |
| `plugin/hooks/stop_gate.py` | `run_checks` | Возвращает описания упавших проверок (пусто, если все прошли). |
| `plugin/skills/adr/scripts/adr.py` | `read_summary` | Название, статус, тип и дата записи; чего не нашли, то прочерк. |
| `plugin/skills/adr/scripts/adr.py` | `slugify` | Имя файла: латиница, цифры и дефисы (русский заголовок транслитерируется). |
| `plugin/skills/analyze-existing/scripts/analyze.py` | `baseline` | Запуск разрешённых инструментов в режиме «только отчёт»; без списка ничего не запускается. |
| `plugin/skills/analyze-existing/scripts/analyze.py` | `changed_path` | Путь из строки состояния «XY путь<TAB>отпечаток» (git берёт пути с пробелами в кавычки). |
| `plugin/skills/analyze-existing/scripts/analyze.py` | `digest` | Отпечаток содержимого файла: правка уже изменённого или нового файла видна в состоянии «до/после». |
| `plugin/skills/analyze-existing/scripts/analyze.py` | `inventory` | Факты о проекте. `save_snapshot=False` не трогает сохранённое состояние «до» (так зовёт его `write`). |
| `plugin/skills/analyze-existing/scripts/analyze.py` | `report_dir_state` | Папка отчёта: новая или созданная этим анализом (с маркером). Чужую папку не трогаем. |
| `plugin/skills/analyze-existing/scripts/analyze.py` | `snapshot` | Состояние дерева по git: изменённые и новые файлы с отпечатками (None, если это не git-репозиторий). |
| `plugin/skills/analyze-existing/scripts/analyze.py` | `snapshot_file` | Где inventory сохраняет состояние «до»: вне проекта, чтобы в проекте ничего не появлялось. |
| `plugin/skills/analyze-existing/scripts/analyze.py` | `write_artifacts` | Пишет в папку отчёта: QUESTIONS.md, PLAN.md, черновики GOAL и решений, facts.json. |
| `plugin/skills/analyze-existing/scripts/analyze_deadcode.py` | `candidates` | Публичные имена, которые в проекте встречаются один раз (только в объявлении). |
| `plugin/skills/analyze-existing/scripts/analyze_deadcode.py` | `file_question` | Простой вопрос про файл: только то, что знает владелец (кто и как запускает), а не то, что видно в коде. |
| `plugin/skills/analyze-existing/scripts/analyze_deadcode.py` | `has_attribute` | Над объявлением C# стоит атрибут (например [HarmonyPatch], [Fact]): его читает фреймворк, а не вызов. |
| `plugin/skills/analyze-existing/scripts/analyze_deadcode.py` | `identifier_counts` | Сколько раз каждое имя встречается во всех текстах проекта (код, настройки, документы). |
| `plugin/skills/analyze-existing/scripts/analyze_deadcode.py` | `is_framework_name` | Обработчик, который зовёт фреймворк по имени (do_GET, Awake, Postfix…): вызова в коде проекта нет и не будет. |
| `plugin/skills/analyze-existing/scripts/analyze_deadcode.py` | `is_generated` | Сгенерированный или декомпилированный код: кандидатов в нём не ищем. |
| `plugin/skills/analyze-existing/scripts/analyze_deadcode.py` | `questions_markdown` | QUESTIONS.md для parch-analysis/: вопросы карточки и вопросы по мёртвому коду. |
| `plugin/skills/analyze-existing/scripts/analyze_equivalents.py` | `find_equivalents` | По строке на каждую роль, которая в проекте есть: где лежит и совпадает ли имя со стандартом. |
| `plugin/skills/analyze-existing/scripts/analyze_history.py` | `git_names` | Имена файлов из последних коммитов (по одной строке на каждое изменение файла). |
| `plugin/skills/analyze-existing/scripts/analyze_history.py` | `has_test` | Есть ли в проекте файл теста с похожим именем (test_<имя>, <Имя>Tests, <имя>.test, <имя>.Tests). |
| `plugin/skills/analyze-existing/scripts/analyze_history.py` | `hotspots` | Топ файлов по (число изменений × число строк); только код, существующий сейчас. |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `by_language` | (швов с участием языка, из них без контрактного теста). |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `call_edges` | Кто кого запускает или подключает: {файл: [файлы проекта, к которым он обращается (путь, импорт, вызов)]}. |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `compile_marker` | Слово игры: обычная подстрока; с приставкой `re:` регулярное выражение (например, `re:Stop-Process[^\n]*\bfm\b`). |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `contract_tests` | (контрактные тесты, тесты, которые только называют шов). |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `executable_text` | Текст теста без комментариев и docstring: имя в пояснении не значит, что тест трогает этот код. |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `file_reasons` | Чем один файл трогает игру, сейвы или `data/`: он в зоне, пишет вне проекта, пишет в зону или называет слово игры. |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `launch_seams` | Швы запуска: код одного языка запускает программу или скрипт другого языка проекта. |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `mentions` | Имя встречается в тексте как отдельное слово (без учёта регистра); слишком короткие имена не считаются. |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `module_root` | Папка модуля: ближайшая папка выше файла с маркером проекта (.csproj, pyproject…), иначе папка верхнего уровня. |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `modules` | Модули по папкам: путь, языки, файлы кода, файлы тестов, назначение, статус. |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `outside_part` | Часть строки, куда идёт запись: у копирования и переноса это приёмник (второй аргумент), источник только читается. |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `outside_writers` | Файлы, которые по тексту пишут за пределы проекта: {путь: [строка с путём, строка с операцией записи]}. |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `outside_writes` | Файлы проекта, которые по тексту пишут за пределы репозитория (папка игры, BepInEx, Steam, другие диски). |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `purpose_of` | Назначение из описания: README папки, описание `__init__.py`, `<Description>` в `.csproj`; иначе вопрос. |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `references` | Тест обращается к файлу кода: путь или имя с расширением, импорт модуля или вызов класса. |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `risk_reasons` | Чем шов трогает игру, сейвы или `data/` (группа «а»): сам файл шва или скрипт, который он запускает или подключает. |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `shared_file_seams` | Швы по общему файлу: одно имя файла данных названо в коде двух и более языков. |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `side_groups` | Стороны шва: для каждого языка файлы этого языка. |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `tainted_names` | Переменные, в которые попал путь вне проекта (прямо или через другую такую переменную): {имя: номер строки}. |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `uses` | Переменная названа в тексте: `$имя` или слово не внутри строки, не после точки (так `.log` не принимается за `log`). |
| `plugin/skills/analyze-existing/scripts/analyze_map.py` | `without_root` | Строка без абсолютных путей самого проекта: путь внутрь своего репозитория не «вне проекта». |
| `plugin/skills/analyze-existing/scripts/analyze_plan.py` | `build_plan` | Строки плана от безопасных к рискованным. Удаления только по ответам владельца (`dead_confirmed`). |
| `plugin/skills/analyze-existing/scripts/analyze_plan.py` | `clean_zone_list` | Список неприкосновенных зон из запроса: только непустые строки, слэши прямые. |
| `plugin/skills/analyze-existing/scripts/analyze_plan.py` | `plan_markdown` | PLAN.md для parch-analysis/: таблица действий и предохранители. |
| `plugin/skills/analyze-existing/scripts/analyze_plan.py` | `touches` | План меняет файл шва или папку, в которой он лежит. |
| `plugin/skills/analyze-existing/scripts/analyze_plan.py` | `validate` | Нарушения предохранителей: каждая строка плана обязана их соблюдать. |
| `plugin/skills/analyze-existing/scripts/analyze_report.py` | `adr_drafts` | Список записей журнала решений как черновики ADR со статусом proposed. |
| `plugin/skills/analyze-existing/scripts/analyze_report.py` | `goal_draft` | Черновик GOAL.md из файла цели проекта (в проект не попадает, лежит в parch-analysis/drafts/). |
| `plugin/skills/analyze-existing/scripts/analyze_report.py` | `health_card` | Карточка здоровья по языкам: у каждого языка только его инструменты. Чего не измеряли, так и пишем. |
| `plugin/skills/analyze-existing/scripts/analyze_report.py` | `lint_part` | Одна фраза по одному инструменту; пусто, если его не запускали. |
| `plugin/skills/analyze-existing/scripts/analyze_report.py` | `not_done` | Строка, если инструмент не запускался или не выполнился; None, если есть результат. |
| `plugin/skills/analyze-existing/scripts/analyze_report.py` | `risks` | 5–10 главных рисков по убыванию веса: что может сломаться и чем это грозит. |
| `plugin/skills/analyze-existing/scripts/analyze_report.py` | `seams_value` | Швы с участием языка; шов без контрактного теста — красный сигнал. |
| `plugin/skills/analyze-existing/scripts/analyze_report.py` | `tests_note` | Тесты, которые запускал анализ (по разрешению владельца), и те, что анализ не запускал: это не провал. |
| `plugin/skills/analyze-existing/scripts/analyze_tools.py` | `config_files` | Файлы настроек проекта: (путь, текст). Только небольшие файлы известных типов. |
| `plugin/skills/analyze-existing/scripts/analyze_tools.py` | `detect` | Для каждого языка проекта: какие инструменты из таблицы подключены (без запуска). |
| `plugin/skills/analyze-existing/scripts/analyze_tools.py` | `dotnet_targets` | Что собирать: решения (.sln/.slnx), а если их нет — каждый .csproj (в корне проекта их часто нет). |
| `plugin/skills/analyze-existing/scripts/analyze_tools.py` | `list_files` | Файлы проекта (относительные пути с «/»), без служебных и сборочных папок. |
| `plugin/skills/analyze-existing/scripts/analyze_tools.py` | `postbuild_steps` | Шаги после сборки (копирование, запуск команд) в .csproj, .props, .targets и Directory.Build.*. |
| `plugin/skills/analyze-existing/scripts/analyze_tools.py` | `resolve_jscpd` | Как запустить jscpd: из PATH; иначе из временной папки вне проекта; иначе (с разрешением) скачать пакет туда. |
| `plugin/skills/analyze-existing/scripts/analyze_tools.py` | `run_baseline` | Запускает разрешённые инструменты в режиме «только отчёт» и возвращает результаты (в файлы проекта не пишет). |
| `plugin/skills/analyze-existing/scripts/analyze_tools.py` | `summarize` | Вывод инструмента в числа (пустой словарь, если вывод не разобрался). |
| `plugin/skills/analyze-existing/scripts/analyze_tools.py` | `write_baseline` | Сохраняет результаты в <папка отчёта>/baseline/ в машинном формате (JSON). Только внутри папки отчёта. |
| `plugin/skills/doctor/scripts/doctor.py` | `hook_scripts_start` | Каждый hook должен запускаться и спокойно отвечать на пустой вызов. |
| `plugin/skills/init-project/scripts/init_project.py` | `create_csharp_files` | Файлы C#-проекта; решение и lock-файлы создаются, если доступен dotnet. |
| `plugin/skills/init-project/scripts/init_project.py` | `create_python_package` | Новый Python-проект: пакет `src/<имя>/__init__.py`, `.importlinter` с контрактом и строка в MODULES.md. |
| `plugin/skills/init-project/scripts/init_project.py` | `create_typescript_files` | Файлы TypeScript-проекта; package-lock.json создаётся, если доступен npm. |
| `plugin/skills/init-project/scripts/init_project.py` | `has_python_tests` | В проекте уже есть тесты Python (`test_*.py` или `*_test.py` вне служебных папок). |
| `plugin/skills/init-project/scripts/init_project.py` | `initial_baseline` | Начальный baseline: известные тесты, пропуски, подавления и настройки каждого языка. |
| `plugin/skills/init-project/scripts/init_project.py` | `is_init_package` | Единственный Python-модуль проекта - нетронутый пакет `src/<имя>/`, созданный самим init. |
| `plugin/skills/init-project/scripts/init_project.py` | `mark_existing_project` | Baseline уже был, а код есть: ставит `existing_project: true`, иначе правила состава проекта падают |
| `plugin/skills/init-project/scripts/init_project.py` | `merge_permissions` | Добавляет правила permissions в .claude/settings.json, не трогая остальные настройки. |
| `plugin/skills/init-project/scripts/init_project.py` | `normalize_target_os` | Целевая ОС: на ней работает исполнитель; CI всё равно на Linux (STANDARD.md, 7.2). |
| `plugin/skills/init-project/scripts/init_project.py` | `package_docstring` | Однострочное описание пакета: из description, иначе из названия проекта; без кавычек и `\`. |
| `plugin/skills/init-project/scripts/init_project.py` | `package_name` | Имя пакета Python из названия проекта: латиница [a-z0-9_], запасное имя `app` при конфликте. |
| `plugin/skills/init-project/scripts/init_project.py` | `parse_goal` | Цель продукта из разговора с владельцем: без неё планирование не начинается (6.5). |
| `plugin/skills/init-project/scripts/init_project.py` | `register_package_module` | Строка пакета в docs/MODULES.md (если её нет) и запись в долг «модуль без блока». |
| `plugin/skills/init-project/scripts/init_project.py` | `render_circleci` | `.circleci/config.yml`: общая часть, по заданию на язык, итоговый `check` с `requires` на все языковые задания. |
| `plugin/skills/init-project/scripts/init_project.py` | `tool_versions_text` | Точные версии инструментов выбранных языков: те же, что в шаблонах CI и настройках. |
| `plugin/skills/trace/scripts/trace.py` | `files_named` | Файлы и папки проекта с таким именем (без пути), пути от корня; служебные папки пропущены. |
| `plugin/skills/trace/scripts/trace.py` | `find_module` | Модуль, чей путь равен цели или является её началом (самый длинный путь побеждает). |
| `plugin/skills/trace/scripts/trace.py` | `project_relative` | Путь от корня проекта: абсолютные пути и `..` приводятся к корню; None, если путь вне проекта. |
| `plugin/templates/ci/parch/parch_catalog.py` | `check` | (провалы, пометки): новая функция без описания и отставший каталог падают; старый долг из baseline допускается. |
| `plugin/templates/ci/parch/parch_catalog.py` | `cs_doc_block` | Комментарий `///` над членом (атрибуты `[...]` между ними пропускаются). |
| `plugin/templates/ci/parch/parch_catalog.py` | `debt_key` | Опознавательный знак функции без описания: файл и имя, без номера строки (строки сдвигаются). |
| `plugin/templates/ci/parch/parch_catalog.py` | `ps_description` | Описание функции: `.SYNOPSIS` в блоке `<# #>` над функцией или в первых строках тела; иначе строка `# ...` над ней. |
| `plugin/templates/ci/parch/parch_catalog.py` | `read_debt` | Функции без описания, записанные владельцем (храповик); None, если файла нет. |
| `plugin/templates/ci/parch/parch_catalog.py` | `render` | Текст каталога: стабильный порядок, чтобы повторная сборка давала тот же файл. |
| `plugin/templates/ci/parch/parch_catalog.py` | `source_files` | Файлы кода проекта без тестов, сборочных и служебных папок (parch-analysis*, tests/projects тоже). |
| `plugin/templates/ci/parch/parch_catalog.py` | `ts_doc_block` | JSDoc `/** ... */` над объявлением (декораторы между ними пропускаются): строки без тегов `@`. |
| `plugin/templates/ci/parch/parch_ci.py` | `accepted_adr_text` | Текст принятых ADR: только они разрешают нестандартные раннеры и матрицы. |
| `plugin/templates/ci/parch/parch_ci.py` | `any_report_outcomes` | Исходы тестов из отчёта любого поддерживаемого формата (по содержимому, без языка). |
| `plugin/templates/ci/parch/parch_ci.py` | `blank_cs_comments_and_strings` | Код C# без комментариев (и без содержимого строк, если keep_strings=False). |
| `plugin/templates/ci/parch/parch_ci.py` | `blank_ps_comments_and_strings` | Код PowerShell без комментариев (и без содержимого строк, если keep_strings=False). |
| `plugin/templates/ci/parch/parch_ci.py` | `blank_python_comments_and_strings` | Код без комментариев и строк, а также тексты настоящих комментариев (через tokenize). |
| `plugin/templates/ci/parch/parch_ci.py` | `blank_ts_comments_and_strings` | Код без комментариев и содержимого строк, а также склеенный текст комментариев. |
| `plugin/templates/ci/parch/parch_ci.py` | `blocked_without_incident_problems` | Блок в статусе blocked или stuck обязан иметь отчёт state/incidents/ДАТА-БЛОК-*.md. |
| `plugin/templates/ci/parch/parch_ci.py` | `check_basis` | «Основания» из коммитов ветки: печатается для описания PR (пишется один раз, в коммите). |
| `plugin/templates/ci/parch/parch_ci.py` | `check_catalog` | Описания публичных функций и свежесть каталога docs/CAPABILITIES.md. |
| `plugin/templates/ci/parch/parch_ci.py` | `check_libraries` | Правило «библиотека в одном модуле» из state/architecture.json. |
| `plugin/templates/ci/parch/parch_ci.py` | `check_module_blocks` | Связь «модуль → блок» (F18): блок существует; новый модуль без блока падает, старый нет. |
| `plugin/templates/ci/parch/parch_ci.py` | `check_skips` | Пропуски считаются по фактическому результату запуска, текстовый поиск идёт дополнительно. |
| `plugin/templates/ci/parch/parch_ci.py` | `check_standard` | Соответствие стандарту: стоимость CI, бюджет текста, отчёты, состав проекта (F14). |
| `plugin/templates/ci/parch/parch_ci.py` | `circleci_problems` | Правила стоимости 7.2 для конфига CircleCI. |
| `plugin/templates/ci/parch/parch_ci.py` | `container_settings` | Отпечатки разделов с настройками проверок в pyproject.toml, setup.cfg, package.json, csproj. |
| `plugin/templates/ci/parch/parch_ci.py` | `contract_modules` | Все модули, названные в контракте (слои дополняются именем контейнера). |
| `plugin/templates/ci/parch/parch_ci.py` | `cs_dead_code` | Находки анализаторов мёртвого кода (IDE0051/0052/0060/0005, CS0169/0414 и др.). |
| `plugin/templates/ci/parch/parch_ci.py` | `cs_entry_point` | Решение или проект, который собирают: решение в корне, иначе единственный проект. |
| `plugin/templates/ci/parch/parch_ci.py` | `cs_modules` | Модули: папки внутри проекта .csproj и файлы кода рядом с ним (пути от корня проекта). |
| `plugin/templates/ci/parch/parch_ci.py` | `cs_rule_calls` | Файлы с правилами ArchUnitNET и все шаблоны пространств имён из их вызовов. |
| `plugin/templates/ci/parch/parch_ci.py` | `cs_run_architecture_tests` | Запускает именно тесты-правила и требует, чтобы хотя бы одно из них реально выполнилось. |
| `plugin/templates/ci/parch/parch_ci.py` | `cs_string_value` | Значение строкового литерала C#: обычного или буквального (@"a""b"). |
| `plugin/templates/ci/parch/parch_ci.py` | `importlinter_config` | Корневые пакеты и контракты из .importlinter или pyproject.toml; None, если конфига нет. |
| `plugin/templates/ci/parch/parch_ci.py` | `incident_budget` | Бюджет инцидентов блока: свой (`incident_budget` в features.json) или из CONSTITUTION.md. |
| `plugin/templates/ci/parch/parch_ci.py` | `incident_budget_problems` | Блок, исчерпавший бюджет инцидентов, обязан иметь статус stuck (его ставит PR с отчётом). |
| `plugin/templates/ci/parch/parch_ci.py` | `incident_problems_by_file` | Нарушения отчётов по файлам: ключ `incident:<имя файла>`, для долга проекта. |
| `plugin/templates/ci/parch/parch_ci.py` | `incident_report_problems` | Отчёты state/incidents/: имя, блок, «Влияние на цель» и «Что нашёл в истории» (6.3, 6.5a). |
| `plugin/templates/ci/parch/parch_ci.py` | `is_broad_pattern` | Исключение, которое закрывает почти весь код: `**`, `src/**`, `**/*.ts`, `*`. |
| `plugin/templates/ci/parch/parch_ci.py` | `is_container` | Файл, в котором настройки проверок лежат лишь в части разделов (любой язык). |
| `plugin/templates/ci/parch/parch_ci.py` | `is_pure_config` | Файл целиком состоит из настроек проверок (любой язык). |
| `plugin/templates/ci/parch/parch_ci.py` | `jest_json_outcomes` | JSON-отчёт Jest и Vitest (--json, --reporter=json): исход каждого теста. |
| `plugin/templates/ci/parch/parch_ci.py` | `junit_outcomes` | JUnit XML (pytest --junitxml, Pester JUnitXml): исход каждого теста. |
| `plugin/templates/ci/parch/parch_ci.py` | `known_skipped` | Пропущенные тесты, известные для всех систем и для этой системы. |
| `plugin/templates/ci/parch/parch_ci.py` | `markdown_section` | Текст раздела `## heading` без HTML-комментариев (None, если раздела нет). |
| `plugin/templates/ci/parch/parch_ci.py` | `modules_with_blocks` | (есть ли колонка «Блок», строки таблицы MODULES.md): путь, статус, блоки модуля. |
| `plugin/templates/ci/parch/parch_ci.py` | `node_bin` | Запуск пакета из node_modules напрямую через node, без обёрток .cmd (одинаково везде). |
| `plugin/templates/ci/parch/parch_ci.py` | `nunit_outcomes` | NUnit 2.5 XML (Pester NUnitXml): исход каждого теста. |
| `plugin/templates/ci/parch/parch_ci.py` | `only_short_files` | jscpd «не проанализировал файлов»: файлы кода есть, но короче порога, а не пути неверны. |
| `plugin/templates/ci/parch/parch_ci.py` | `parsed_root` | Корень XML-отчёта; чужой или повреждённый файл это ошибка, а не «тестов нет». |
| `plugin/templates/ci/parch/parch_ci.py` | `partial_mismatch` | Не даёт выдать урезанный отчёт за полный и наоборот: храповик различает режимы. |
| `plugin/templates/ci/parch/parch_ci.py` | `platform_key` | Ключ baseline для пропусков, зависящих от системы (тест только для Windows и т.п.). |
| `plugin/templates/ci/parch/parch_ci.py` | `powershell_exe` | Только PowerShell 7 (`pwsh`): Windows PowerShell 5.1 не используется (ADR-0010). |
| `plugin/templates/ci/parch/parch_ci.py` | `py_modules` | Модули: пакеты и одиночные .py-файлы прямо в корне кода (пути от корня проекта). |
| `plugin/templates/ci/parch/parch_ci.py` | `py_report_key` | Идентификатор pytest `tests/a.py::Класс::имя[пар]` в виде ключа отчёта JUnit. |
| `plugin/templates/ci/parch/parch_ci.py` | `py_run_outcomes` | Запускает тесты и возвращает исход каждого из отчёта JUnit XML. |
| `plugin/templates/ci/parch/parch_ci.py` | `py_source_roots` | Корни кода: [tool.parch] source_roots в pyproject.toml, иначе `src`, иначе корень. |
| `plugin/templates/ci/parch/parch_ci.py` | `py_test_ids` | Тесты Python собираются самим pytest; отчёт не нужен. |
| `plugin/templates/ci/parch/parch_ci.py` | `report_gaps` | Тесты из кода и из baseline, которых нет в отчёте запуска. |
| `plugin/templates/ci/parch/parch_ci.py` | `report_is_partial` | True, если отчёт помечен как урезанный (JUnit XML с именем набора parch-partial). |
| `plugin/templates/ci/parch/parch_ci.py` | `rule_patterns` | Регулярные выражения путей из правила: (откуда `from` или куда `to`, шаблон). |
| `plugin/templates/ci/parch/parch_ci.py` | `scan_counts` | Сколько раз встречается каждый вид (по файлам): «файл/вид» -> число. |
| `plugin/templates/ci/parch/parch_ci.py` | `search_sources` | Источники раздела «Где искал», у которых есть запрос или ссылка (пустая метка не в счёт). |
| `plugin/templates/ci/parch/parch_ci.py` | `source_roots` | Корни кода проекта для языка (для остальных языков пока весь проект). |
| `plugin/templates/ci/parch/parch_ci.py` | `standard_workflow_problems` | Нарушения правил 7.2 в одном workflow: таймаут, отмена, триггеры, раннеры, матрицы. |
| `plugin/templates/ci/parch/parch_ci.py` | `target_os_problem` | В CONSTITUTION.md должна быть записана целевая ОС (STANDARD.md, 7.2, правило 2). |
| `plugin/templates/ci/parch/parch_ci.py` | `test_outcomes` | Исходы тестов из отчёта запуска; None, если для языка отчёт нужен, а его не передали. |
| `plugin/templates/ci/parch/parch_ci.py` | `trx_outcomes` | TRX (dotnet test --logger trx): исход каждого теста. |
| `plugin/templates/ci/parch/parch_ci.py` | `trx_report_outcomes` | Исходы тестов из файла TRX или из папки с файлами TRX (по одному на тестовый проект). |
| `plugin/templates/ci/parch/parch_ci.py` | `ts_modules` | Модули: папки и одиночные файлы кода прямо в корне кода (пути от корня проекта). |
| `plugin/templates/ci/parch/parch_ci.py` | `unresolved_modules` | Модули из контрактов, которых нет ни в коде, ни среди установленных пакетов. |
| `plugin/templates/ci/parch/parch_ci.py` | `update_baseline` | Обновляет baseline; only_tests: только списки тестов и пропусков (репозиторий продукта). |
| `plugin/templates/ci/parch/parch_ci.py` | `with_tests` | Отчёт без единого теста нельзя принимать: пустой файл скрыл бы любые пропуски. |
| `plugin/templates/ci/parch/parch_ci.py` | `yaml_children` | Строки, вложенные глубже заголовка в строке `start` (пустые и комментарии пропускаются). |
| `plugin/templates/ci/parch/parch_ci.py` | `yaml_keys` | Ключи верхнего уровня строк: имя -> (значение в строке, вложенные строки). |
| `plugin/templates/ci/parch/parch_libraries.py` | `check` | (провалы, пометки) для правил одного языка. |
| `plugin/templates/ci/parch/parch_libraries.py` | `modules_in` | Имена библиотек, подключённых строкой (комментарии уже отброшены). |
| `plugin/templates/ci/parch/parch_libraries.py` | `read_rules` | (правила, проблемы формата). Нет файла: правил нет, проблем нет. |
| `plugin/templates/ci/parch/parch_standard.py` | `accepted_by_owner` | Блок без тестов принят владельцем: все критерии отмечены и стоит строка итога. |
| `plugin/templates/ci/parch/parch_standard.py` | `basis_problem` | Раздел «Основания» в тексте (описание PR или сообщение коммита): есть, не пуст, назван источник. |
| `plugin/templates/ci/parch/parch_standard.py` | `basis_section_lines` | Строки раздела «Основания» в тексте (None: раздела нет). |
| `plugin/templates/ci/parch/parch_standard.py` | `changed_files` | Файлы, изменённые веткой относительно основной (`git diff base...HEAD`), и пояснение, если история недоступна. |
| `plugin/templates/ci/parch/parch_standard.py` | `check` | (провалы, пометки): правила состава проекта в трёх режимах: новый проект, существующий без долга, с храповиком. |
| `plugin/templates/ci/parch/parch_standard.py` | `commit_messages` | Сообщения коммитов ветки относительно основной (`git log base..HEAD`) и пояснение, если история недоступна. |
| `plugin/templates/ci/parch/parch_standard.py` | `commits_basis_problem` | «Основания» хотя бы в одном коммите ветки (не в каждом); без новых коммитов или без истории проверка красная. |
| `plugin/templates/ci/parch/parch_standard.py` | `commits_basis_text` | Раздел «Основания» из последнего коммита ветки, где он корректен (для описания PR: пишется один раз, в коммите). |
| `plugin/templates/ci/parch/parch_standard.py` | `commits_protected_problem` | Если ветка меняет защищённые файлы, в коммитах есть раздел с их названием и причиной (владелец утверждает такие PR). |
| `plugin/templates/ci/parch/parch_standard.py` | `cyclic_groups` | Все циклы: группы блоков, зависящих друг от друга по кругу (компоненты связности Тарьяна). |
| `plugin/templates/ci/parch/parch_standard.py` | `features_violations` | Связность реестра блоков (раздел 3): цель, зависимости, цикл, «готово» без тестов приёмки. |
| `plugin/templates/ci/parch/parch_standard.py` | `is_managed` | Подключённый проект: есть CONSTITUTION.md (как в hooks) или установлен `.github/parch/parch_ci.py`. |
| `plugin/templates/ci/parch/parch_standard.py` | `is_protected` | Файл, который в проекте утверждает владелец: CI, правила, настройки Claude, state/, цель и инструкции. |
| `plugin/templates/ci/parch/parch_standard.py` | `mentioned` | `name` стоит в тексте отдельным путём: `AGENTS.md` не засчитывается в `docs/AGENTS.md` и `NOT_AGENTS.md`. |
| `plugin/templates/ci/parch/parch_standard.py` | `named_in` | Файл назван сам или его защищённой папкой (`.github/parch/`, `state/`); `docs/` и `src/` не защищены, не в счёт. |
| `plugin/templates/ci/parch/parch_standard.py` | `protected_section_text` | Текст раздела «Защищённые файлы…» в сообщении коммита (`## Защищённые файлы…` или строка «Защищённые файлы…:»). |
| `plugin/templates/ci/parch/parch_standard.py` | `strings` | Непустые элементы списка как строки (не список: пусто). |
| `plugin/templates/ci/parch/parch_standard.py` | `write_debt` | Файл остаётся и при пустом долге: иначе существующий проект вернулся бы к предупреждению. |
| `plugin/templates/ci/parch/parch_state.py` | `annotate` | Пометка вверху табло, сразу после заголовка. |
| `plugin/templates/ci/parch/parch_state.py` | `failed_tests` | Число упавших тестов в отчёте любого формата (JUnit, JSON, trx). |
| `plugin/templates/ci/parch/parch_state.py` | `publish` | Публикует табло на ветку `status`; возвращает сообщение. Не падает, если публикации нет. |
| `plugin/templates/ci/parch/parch_state.py` | `repo_from_origin` | `владелец/репозиторий` по адресу origin (в нём может быть токен: наружу идёт только имя). |
| `plugin/templates/ci/parch/parch_state.py` | `tests_exist` | В проекте есть тесты: baseline перечисляет хотя бы один (его заводит init). |
| `plugin/templates/ci/parch/parch_state.py` | `verdict` | Итог тестов по коду выхода и отчёту (решение владельца, 2026-10-06). |
| `plugin/templates/ci/parch/parch_state.py` | `write_key` | Ключ записи от `add_ssh_keys` (`~/.ssh/id_rsa_<отпечаток>`); без него публикации нет. |
| `plugin/templates/ci/parch/parch_status.py` | `acceptance_state` | absent: файла приёмки нет; pending: критерии не отмечены или нет итога; accepted: принято. |
| `plugin/templates/ci/parch/parch_status.py` | `block_passes` | Блок прошёл, если тесты приёмки прошли, а если есть файл приёмки, то и владелец принял. |
| `plugin/templates/ci/parch/parch_status.py` | `ci_section` | Раздел «Расход CI»: минуты за неделю и на один PR по данным о прогонах Actions. |
| `plugin/templates/ci/parch/parch_status.py` | `matches` | Относится ли тест `key` из отчёта к файлу или классу приёмки `entry`. |
| `plugin/templates/ci/parch/parch_status.py` | `normalize_pr` | PR из `gh pr list --json number,title,isDraft,labels,statusCheckRollup` или уже готовый. |
| `plugin/templates/ci/parch/parch_status.py` | `parch_ci_module` | Общие правила разбора (бюджет, разделы отчётов) живут в parch_ci.py рядом с этим файлом. |
| `plugin/templates/ci/parch/parch_status.py` | `plural` | 1 инцидент, 2-4 инцидента, 5 и 11-14 инцидентов. |
| `plugin/templates/ci/parch/parch_status.py` | `report_warning` | Предупреждение, если отчёт тестов снят не с содержимого текущего коммита ("" = всё в порядке). |
| `plugin/templates/ci/parch/parch_status.py` | `run_minutes` | Минуты квоты одного прогона: каждое задание округляется вверх, Windows x2, macOS x10. |
| `plugin/templates/ci/parch/parch_status.py` | `stuck_card` | Карточка решения для застрявшего блока (STANDARD.md, 6.6): три варианта ответа одним словом. |
| `plugin/templates/ci/parch/parch_status.py` | `traceability_lines` | Раздел «Прослеживаемость»: модули без блока (кандидаты на удаление) и блоки без модулей (F18). |
| `scripts/preflight.py` | `changed_files` | Файлы, изменённые относительно origin/main, включая неотслеживаемые (None: git не ответил). |
| `scripts/preflight.py` | `code_changed` | True, если PR меняет что-то кроме текста; при любой неясности считается, что меняет. |
| `scripts/preflight.py` | `plan` | Шаги проверки: дешёвые первыми, полный прогон последним. |
| `scripts/preflight.py` | `release_note_command` | Сообщение о расхождении версии плагина и тега; без `--strict`, поэтому шаги не останавливает. |
| `scripts/preflight.py` | `run` | Выполняет шаги по порядку; код первого упавшего шага становится кодом выхода. |
| `scripts/release_check.py` | `Report.drift` | Файлы плагина изменились после тега, а версия осталась прежней. |
| `scripts/release_check.py` | `render` | Строки для вывода и код выхода. |
