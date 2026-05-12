# store/gigachat_service.py
import json
import logging
from django.conf import settings
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole, Function, FunctionCall
from gigachat.exceptions import GigaChatException
import httpx  # вместо requests
from store.models import Product
from .models import ChatSession, ChatMessage
import re

from .product_utils import search_products, get_product_details

logger = logging.getLogger(__name__)
MAX_TOOL_CALLS = 8

SYSTEM_PROMPT = """Ты — AI-консультант магазина «Music Store». Твоя задача — быстро подбирать реальные товары из каталога и предоставлять исчерпывающую информацию, комбинируя данные из базы магазина и интернета.

**Жёсткие правила:**
1. **Никогда не выдумывай** названия, бренды, цены или характеристики. Используй только данные из `search_products`, `get_product_details` и `search_web`. Если данных нет ни в базе, ни в интернете, честно сообщи об этом.
2. Если `search_products` возвращает пустой список, скажи: «По вашему запросу ничего не найдено. Попробуйте изменить бюджет или другие параметры». **Не предлагай вымышленных моделей.**
3. **Никогда не ищи «готовые комплекты».** В базе их нет. Твоя задача — подбирать товары по отдельности.
4. **Запросы по бренду**: если пользователь спрашивает о товарах конкретного бренда (например, «Fender», «Yamaha»), немедленно вызывай `search_products(query="название_бренда")` без дополнительных фильтров. Если пользователь уточняет тип инструмента (например, «гитары Fender»), добавь соответствующий параметр (`instrument_type='guitar'`).
5. Если пользователь просит «покажи», «найди», «есть ли», «посоветуй», «подбери» товары или товары бренда, но НЕ просит характеристики/подробности, отвечай только по результатам `search_products`: название, категория, цена, наличие и ссылка. Не вызывай `get_product_details` и `search_web`, не перечисляй характеристики и не добавляй сведения из интернета.
6. Никогда не пиши, что товар идёт «в комплекте» с другим товаром, если это прямо не указано в названии, описании или features товара. Готовых комплектов в базе нет.
7. Если пользователь просит общий совет, инструкцию или объяснение без намерения купить товар (например, «как ухаживать за гитарой», «как настроить барабаны», «чем отличается акустическая гитара от классической»), **не вызывай `search_products`**. Ответь как консультант общими практическими рекомендациями.

**Обработка запросов с двумя товарами (инструмент + аксессуар):**
- Пользователь может попросить пару: скрипка + смычок, барабаны + палочки, гитара + усилитель, клавишные + стойка и т.п.
- **Сразу делай два вызова `search_products`:**
  1. Поиск основного инструмента (по ключевым словам, типу, бюджету).
  2. Поиск сопутствующего товара (по ключевым словам, например «смычок», «палочки», «усилитель», «стойка»).
- После получения результатов **сразу** формируй ответ с конкретными моделями, ценами и ссылками.
- **Не спрашивай бюджет, если он не указан.** Вместо этого предлагай варианты в среднем ценовом сегменте (например, инструменты до 50 000 руб., аксессуары до 15 000 руб.).

**Уточнение критериев (бюджет, тип гитары и т.д.):**
- При любом уточнении **сразу заново вызывай `search_products` для каждого товара** с новыми значениями, сохраняя остальные параметры из предыдущего успешного поиска.
- **Пример:** если ранее были найдены гитара и усилитель, а пользователь говорит «бюджет до 100 тысяч», сделай два вызова:
  1. `search_products(category='Электрогитары', budget_max=70000)`
  2. `search_products(category='Усилители', budget_max=30000)`
- Не переводи категорию на английский (не используй 'Equipment' вместо 'Усилители'). Если сомневаешься в названии категории, используй `query='усилитель'`.

**Ответы на запросы о характеристиках и деталях — строго обязательно:**
- Когда пользователь явно просит «характеристики», «подробности», «расскажи про эту модель», «какая мощность» и т.п., ты выполняешь **строгую последовательность действий**:
  1. Вызови `get_product_details` с ID товара.
  2. **Немедленно после этого вызови `search_web`**, сформулировав запрос как полное название товара + ключевое слово («характеристики», «мощность», «материал» и т.д.).
  3. **Только после получения результатов обоих вызовов** сформируй единый ответ, включив в него цену, наличие, данные из `features` (если есть) и найденную в интернете информацию.
- **Категорически запрещено писать ответ, пока не выполнен вызов `search_web`.**
- Если `search_web` вернул пустой результат, скажи: «Дополнительных сведений в интернете не найдено» и выведи только данные из базы.
- **Никогда не спрашивай разрешения** – действуй.

**Формат ответа:**
- Используй переносы строк (`\n`) для удобочитаемости.
- При перечислении товаров каждый пункт начинай с новой строки, используя маркер `•`.
- Пример оформления:
        Подобрал для вас комплект:
        • Электрогитара Fender Squier Affinity Telecaster — 41 790 руб.
        • Комбоусилитель Marshall CODE 25 — 46 000 руб.
        Общая стоимость: 87 790 руб.
        Подходит? Или показать другие варианты?
- Если характеристика найдена в интернете, просто приведи её, не упоминая, откуда она взята. Не пиши фразы вроде «В каталоге: не указана» или «По данным из интернета».
- Никогда не показывай пользователю ID товара. ID используются только для внутренних вызовов функций.
- В ответах с характеристиками не сообщай пользователю, что в каталоге чего-то нет. Сразу выдавай итоговую информацию, полученную любым способом. Не используй фразы «В базе не указано», «Провожу дополнительный поиск» и т.п.

**Примеры диалогов:**
*Пользователь:* подбери скрипку со смычком
*Ты:* (вызовы `search_products`) → «Скрипка Yamaha V5 — 25 000 руб. Смычок Stagg — 1 500 руб. Общая сумма 26 500 руб. Подходит?»

*Пользователь:* нужна электрогитара и комбик до 80 тысяч
*Ты:* (вызовы с `budget_max`) → «Электрогитара Fender Squier Affinity — 41 790 руб. Комбоусилитель Marshall MG15 — 12 500 руб. Итого 54 290 руб. — вписываемся в бюджет. Показать другие варианты?»

*Пользователь:* какая у него мощность?
*Ты:* (вызываешь `get_product_details`, видишь пустое `features` → сразу `search_web(query='Marshall CODE 25 мощность')`) → «Комбоусилитель Marshall Code 25: цена 46 000 руб., в наличии. Мощность (по данным из интернета): 25 Вт, транзисторный, два канала...»

*Пользователь:* покажи товары Fender
*Ты:* (вызов `search_products(query='Fender')`) → «Бренд Fender представлен...»

**Работа с бюджетом комплекта:**
- При общем бюджете (например, "до 100 тысяч") подбирай такие пары товаров, сумма цен которых не превышает бюджет.
- Если в каталоге есть подходящие комбинации, сразу предлагай их.
- Если подходящих комбинаций нет, честно скажи и предложи изменить бюджет или выбрать что-то одно.

Помни: твоя сила — в честности, скорости и проактивности. Не затягивай диалог, давай полную информацию сразу."""

SALES_ANALYTICS_PROMPT = """Ты — AI-аналитик интернет-магазина музыкальных инструментов «Music Store».
Тебе передают агрегированные данные из админки: продажи, отмены, тренды, остатки, популярные товары и прогноз спроса.

Правила:
1. Не выдумывай цифры, товары, бренды и причины. Используй только переданные данные.
2. Пиши для администратора магазина коротко и практично.
3. Дай 4-6 пунктов: что растет, что проседает, какие товары продвигать, какие остатки проверить, на какие отмены обратить внимание.
4. Отдельно выдели прогноз на ближайший месяц по товарам из данных demand_forecast.
5. Если данных мало, прямо скажи, каких данных не хватает.
6. Не используй Markdown-таблицы.
"""

class GigaChatService:
    def __init__(self):
        # Для локальной разработки можно оставить verify_ssl_certs=False,
        # но в production нужно настроить сертификаты Минцифры.
        self.client = GigaChat(
            credentials=settings.GIGACHAT_CREDENTIALS,
            verify_ssl_certs=False  # ⚠️ Для продакшена заменить на True и настроить сертификаты
        )
        # Определение функций для Function Calling
        self.functions = [
            Function(
                name="search_products",
                description="Поиск музыкальных инструментов в каталоге магазина по заданным критериям.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Текстовый поисковый запрос. Используй для поиска по названию товара, описанию, категории или **бренду**. Например: 'Fender', 'гитара', 'Yamaha'."
                        },
                        "category": {
                            "type": "string",
                            "description": "Категория товара (например, 'Электрогитары', 'Акустические гитары', 'Усилители', 'Комбоусилители')."
                        },
                        "budget_max": {
                            "type": "number",
                            "description": "Максимальный бюджет пользователя в рублях."
                        },
                        "skill_level": {
                            "type": "string",
                            "enum": ["beginner", "amateur", "professional"],
                            "description": "Уровень подготовки музыканта."
                        },
                        "instrument_type": {
                            "type": "string",
                            "enum": ["guitar", "piano", "drums", "violin", "wind", "equipment", "accessories"],
                            "description": "Тип музыкального инструмента (например, 'guitar' для гитар, 'piano' для клавишных)."
                        },
                        "exclude_instrument_types": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["guitar", "piano", "drums", "violin", "wind", "equipment", "accessories"]},
                            "description": "Список типов инструментов, которые нужно исключить из поиска."
                        },
                        "guitar_subtype": {
                            "type": "string",
                            "enum": ["electric", "acoustic", "bass"],
                            "description": "Подтип гитары: electric (электрогитара), acoustic (акустическая), bass (бас-гитара). Используй, когда пользователь явно указывает тип."
                        }
                    }
                }
            ),
            Function(
                name="get_product_details",
                description="Получение подробной информации о конкретном товаре по его идентификатору.",
                parameters={
                    "type": "object",
                    "properties": {
                        "product_id": {
                            "type": "integer",
                            "description": "Уникальный идентификатор товара в базе данных."
                        }
                    },
                    "required": ["product_id"]
                }
            ),
            Function(
                name="search_web",
                description="Поиск информации о товаре или бренде в интернете. Используй, когда в каталоге недостаточно характеристик.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Поисковый запрос на русском языке (например, 'Fender Squier Affinity Telecaster характеристики')."
                        }
                    },
                    "required": ["query"]
                }
            )
        ]

    def analyze_sales(self, analytics_data: dict) -> str:
        messages = [
            Messages(role=MessagesRole.SYSTEM, content=SALES_ANALYTICS_PROMPT),
            Messages(
                role=MessagesRole.USER,
                content=(
                    "Проанализируй отчет продаж и сформируй прогнозные рекомендации.\n"
                    f"Данные:\n{json.dumps(analytics_data, ensure_ascii=False, default=str)}"
                )
            )
        ]

        try:
            response = self.client.chat(Chat(messages=messages))
            return response.choices[0].message.content
        except GigaChatException as e:
            logger.error(f"GigaChat sales analytics error {e.status_code}: {e}")
            return "AI-аналитик временно недоступен. Попробуйте открыть отчет позже."
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
            logger.error(f"GigaChat sales analytics network error: {e}")
            return "AI-аналитик временно недоступен из-за ошибки сети. Попробуйте позже."
        except Exception:
            logger.exception("Unexpected sales analytics error")
            return "Не удалось сформировать AI-прогноз. Проверьте настройки GigaChat и повторите попытку."

    def _run_search_web(self, q: str):
        q = " ".join(q.split())

        try:
            api_key = settings.TAVILY_API_KEY

            payload = {
                "query": q,
                "search_depth": "basic",
                "max_results": 5,
                "include_answer": False,
                "include_raw_content": False,
            }

            with httpx.Client(timeout=15) as client:
                resp = client.post(
                    "https://api.tavily.com/search",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )

            if resp.status_code != 200:
                logger.error(
                    "Tavily Search error %s: %s",
                    resp.status_code,
                    resp.text[:1000],
                )
                return {
                    "query": q,
                    "error": f"Ошибка интернет-поиска Tavily: {resp.status_code}",
                }

            data = resp.json()
            results = data.get("results", [])

            logger.info("Tavily query='%s', results=%s", q, len(results))

            if not results:
                return {
                    "query": q,
                    "snippet": "Информация не найдена.",
                }

            snippets = []

            for item in results[:5]:
                title = item.get("title", "")
                content = item.get("content", "")
                url = item.get("url", "")

                snippets.append(
                    f"{title}: {content}\n{url}"
                )

            return {
                "query": q,
                "snippet": "\n".join(snippets)[:2000],
            }

        except Exception as e:
            logger.exception("Tavily Search failed")
            return {
                "query": q,
                "error": f"Не удалось выполнить поиск Tavily: {str(e)}",
            }

    def _is_brand_info_request(self, text: str) -> bool:
        text = text.lower().strip()

        # Не считаем запросом про бренд обращения к последнему товару
        product_reference_phrases = [
            "про нее",
            "про него",
            "о ней",
            "о нем",
            "про эту модель",
            "об этой модели",
            "про товар",
            "об этом товаре",
            "с id",
            "id ",
        ]

        if any(phrase in text for phrase in product_reference_phrases):
            return False

        brand_keywords = [
            "про фирму",
            "о фирме",
            "фирма",
            "бренд",
            "о бренде",
            "про бренд",
            "компания",
            "производитель",
        ]

        if any(keyword in text for keyword in brand_keywords):
            return True

        # Например: "расскажи про ibanez", "расскажи о yamaha"
        if text.startswith("расскажи про ") or text.startswith("расскажи о "):
            return True

        return False


    def _is_detail_request(self, text: str) -> bool:
        text = text.lower().strip()

        # Если пользователь спрашивает про фирму/бренд,
        # НЕ считаем это запросом подробностей последнего товара.
        if self._is_brand_info_request(text):
            return False

        detail_keywords = [
            "подробнее",
            "подробности",
            "характеристики",
            "характеристика",
            "какая мощность",
            "какой материал",
            "какие параметры",
            "про нее",
            "про него",
            "о ней",
            "о нем",
            "расскажи про нее",
            "расскажи про него",
            "расскажи про эту модель",
            "расскажи об этой модели",
            "расскажи про товар",
            "расскажи об этом товаре",
        ]

        return any(keyword in text for keyword in detail_keywords)

    def _is_general_advice_request(self, text: str) -> bool:
        text = text.lower().strip()

        product_search_keywords = [
            "покажи",
            "найди",
            "есть ли",
            "посоветуй товар",
            "подбери",
            "купить",
            "сколько стоит",
            "цена",
            "до ",
            "руб",
        ]

        if any(keyword in text for keyword in product_search_keywords):
            return False

        advice_keywords = [
            "как ухаживать",
            "уход",
            "как чистить",
            "как хранить",
            "как настроить",
            "как выбрать",
            "чем отличается",
            "что лучше",
            "как пользоваться",
            "как обслуживать",
            "как менять струны",
        ]

        return any(keyword in text for keyword in advice_keywords)

    def _build_general_advice_answer(self, user_message: str) -> str:
        text = user_message.lower()

        if "гитар" in text:
            return (
                "Чтобы гитара дольше сохраняла строй, внешний вид и удобство игры:\n"
                "• Храните ее в чехле или кейсе, подальше от батарей, солнца и резких перепадов температуры.\n"
                "• После игры протирайте струны, гриф и корпус мягкой сухой микрофиброй.\n"
                "• Меняйте струны, когда они потемнели, плохо строят или стали неприятными на ощупь.\n"
                "• Следите за влажностью: для деревянных инструментов комфортно примерно 40-55%.\n"
                "• Не используйте бытовую химию. Для корпуса, накладки грифа и струн лучше брать специальные средства для музыкальных инструментов.\n"
                "• Раз в несколько месяцев проверяйте высоту струн, прогиб грифа и состояние ладов. Если струны звенят или играть стало тяжело, лучше сделать настройку у мастера.\n"
                "• Перед перевозкой ослабьте риск ударов: используйте плотный чехол или жесткий кейс.\n\n"
                "Если скажете, какая у вас гитара — акустическая, классическая, электрогитара или бас — дам более точный уход."
            )

        return (
            "Могу подсказать общие рекомендации:\n"
            "• Храните инструмент в чехле или кейсе, без влаги, перегрева и резких перепадов температуры.\n"
            "• После использования протирайте поверхности мягкой сухой тканью.\n"
            "• Используйте только средства ухода, предназначенные для музыкальных инструментов.\n"
            "• Регулярно проверяйте расходники, крепления, настройку и состояние рабочих частей.\n"
            "• Для сложной регулировки лучше обратиться к мастеру, чтобы не повредить инструмент.\n\n"
            "Уточните инструмент, и я дам конкретный чек-лист ухода."
        )


    def _get_last_found_product_from_session(self, session):
        function_messages = ChatMessage.objects.filter(
            session=session,
            role="function",
            function_name="search_products"
        ).order_by("-created_at")

        for msg in function_messages:
            try:
                data = json.loads(msg.content)
            except Exception:
                continue

            if isinstance(data, list) and len(data) > 0:
                return data[0]

        return None
    
    def _extract_product_id_from_text(self, text: str):
        match = re.search(r'\bID\s*(\d+)\b', text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def _is_bass_guitar_request(self, text: str) -> bool:
        text = text.lower()
        return any(keyword in text for keyword in ("бас", "bass"))

    def _normalize_search_products_args(self, user_message: str, function_args: dict) -> dict:
        function_args = dict(function_args or {})

        if self._is_bass_guitar_request(user_message):
            function_args["instrument_type"] = "guitar"
            function_args["guitar_subtype"] = "bass"
            function_args["category"] = "Басгитары"
            function_args.setdefault("query", "бас")

        return function_args

    def process_message_with_products(self, user_message: str, session_id: str, chat_history: list = None):
        # Получаем или создаём сессию
        session, _ = ChatSession.objects.get_or_create(session_key=session_id)

        # Формируем messages для API
        messages = []
        messages.append(Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT))

        # Загружаем сохранённую историю из БД с корректным восстановлением function_call
        from gigachat.models import FunctionCall
        saved_messages = list(ChatMessage.objects.filter(session=session).order_by('created_at'))
        i = 0
        while i < len(saved_messages):
            msg = saved_messages[i]
            if msg.role == 'user':
                messages.append(Messages(role=MessagesRole.USER, content=msg.content))
            elif msg.role == 'assistant':
                if msg.function_name:  # это вызов функции
                    # Ожидаем, что следующее сообщение – результат функции
                    if i + 1 < len(saved_messages) and saved_messages[i + 1].role == 'function':
                        func_msg = saved_messages[i + 1]
                        # Создаём правильную пару assistant + function
                        messages.append(Messages(
                            role=MessagesRole.ASSISTANT,
                            content="",
                            function_call=FunctionCall(name=msg.function_name, arguments={})
                        ))
                        messages.append(Messages(
                            role=MessagesRole.FUNCTION,
                            content=func_msg.content,
                            name=func_msg.function_name
                        ))
                        i += 1  # пропускаем function
                    else:
                        # Некорректная запись – пропускаем этот assistant
                        pass
                else:
                    messages.append(Messages(role=MessagesRole.ASSISTANT, content=msg.content))
            # Сообщения с role='function' обрабатываются только в паре с assistant выше
            i += 1

        # Если истории в БД нет, используем историю с фронта.
        # Это нужно для анонимного пользователя, у которого история хранится в sessionStorage.
        if not saved_messages and chat_history:
            for msg in chat_history[-10:]:
                role = msg.get('role')
                content = msg.get('content', '')

                if not content:
                    continue

                if role == 'user':
                    messages.append(Messages(role=MessagesRole.USER, content=content))
                elif role == 'assistant':
                    messages.append(Messages(role=MessagesRole.ASSISTANT, content=content))

        # Добавляем текущее сообщение пользователя
        messages.append(Messages(role=MessagesRole.USER, content=user_message))

        # Сохраняем сообщение пользователя в БД
        ChatMessage.objects.create(session=session, role='user', content=user_message)

        final_answer = ""
        found_products = []

        if self._is_general_advice_request(user_message):
            final_answer = self._build_general_advice_answer(user_message)
            ChatMessage.objects.create(session=session, role='assistant', content=final_answer)
            return final_answer, found_products

        allow_details = self._is_detail_request(user_message) or bool(self._extract_product_id_from_text(user_message))
        allow_brand_web = self._is_brand_info_request(user_message)

        if self._is_brand_info_request(user_message):
            web_query = f"{user_message} музыкальные инструменты официальный сайт история"

            web_function_call = FunctionCall(
                name="search_web",
                arguments={"query": web_query}
            )

            ChatMessage.objects.create(
                session=session,
                role="assistant",
                content="",
                function_name="search_web",
            )

            messages.append(Messages(
                role=MessagesRole.ASSISTANT,
                content="",
                function_call=web_function_call
            ))

            web_result = self._run_search_web(web_query)

            ChatMessage.objects.create(
                session=session,
                role="function",
                content=json.dumps(web_result, ensure_ascii=False),
                function_name="search_web",
            )

            messages.append(Messages(
                role=MessagesRole.FUNCTION,
                content=json.dumps(web_result, ensure_ascii=False),
                name="search_web"
            ))

        explicit_product_id = self._extract_product_id_from_text(user_message)

        if self._is_detail_request(user_message) or explicit_product_id:
            last_product = self._get_last_found_product_from_session(session)

            if explicit_product_id:
                product_id = explicit_product_id
            elif last_product:
                product_id = last_product.get("id")
            else:
                product_id = None

            if product_id and Product.objects.filter(id=product_id).exists():
                details_result = get_product_details(product_id=product_id)

                # Добавляем get_product_details в историю сообщений для модели
                details_function_call = FunctionCall(
                    name="get_product_details",
                    arguments={"product_id": product_id}
                )

                ChatMessage.objects.create(
                    session=session,
                    role="assistant",
                    content="",
                    function_name="get_product_details",
                )

                messages.append(Messages(
                    role=MessagesRole.ASSISTANT,
                    content="",
                    function_call=details_function_call
                ))

                ChatMessage.objects.create(
                    session=session,
                    role="function",
                    content=json.dumps(details_result, ensure_ascii=False),
                    function_name="get_product_details",
                )

                messages.append(Messages(
                    role=MessagesRole.FUNCTION,
                    content=json.dumps(details_result, ensure_ascii=False),
                    name="get_product_details"
                ))

                product_name = details_result.get("name", "")
                web_query = f"{product_name} характеристики"

                web_function_call = FunctionCall(
                    name="search_web",
                    arguments={"query": web_query}
                )

                ChatMessage.objects.create(
                    session=session,
                    role="assistant",
                    content="",
                    function_name="search_web",
                )

                messages.append(Messages(
                    role=MessagesRole.ASSISTANT,
                    content="",
                    function_call=web_function_call
                ))

                web_result = self._run_search_web(web_query)

                ChatMessage.objects.create(
                    session=session,
                    role="function",
                    content=json.dumps(web_result, ensure_ascii=False),
                    function_name="search_web",
                )

                messages.append(Messages(
                    role=MessagesRole.FUNCTION,
                    content=json.dumps(web_result, ensure_ascii=False),
                    name="search_web"
                ))

        try:
            for _ in range(MAX_TOOL_CALLS):
                response = self.client.chat(Chat(
                    messages=messages,
                    functions=self.functions,
                    function_call="auto"
                ))
                response_message = response.choices[0].message

                if not response_message.function_call:
                    final_answer = response_message.content
                    # Сохраняем финальный ответ
                    ChatMessage.objects.create(session=session, role='assistant', content=final_answer)
                    break

                # Сохраняем запрос функции (роль assistant с пустым content)
                ChatMessage.objects.create(session=session, role='assistant', content='', function_name=response_message.function_call.name)
                messages.append(Messages(
                    role=MessagesRole.ASSISTANT,
                    content="",
                    function_call=response_message.function_call
                ))

                function_name = response_message.function_call.name
                raw_args = response_message.function_call.arguments
                try:
                    if isinstance(raw_args, str):
                        function_args = json.loads(raw_args or "{}")
                    else:
                        function_args = raw_args or {}
                except json.JSONDecodeError:
                    function_args = {}

                if not isinstance(function_args, dict):
                    function_args = {}

                if function_name in ["get_product_details", "search_web"] and not allow_details and not allow_brand_web:
                    function_result_message = {
                        "error": "tool_not_allowed_for_this_request",
                        "message": "Пользователь просил показать или подобрать товары, а не характеристики. Ответь только по результатам search_products."
                    }

                    ChatMessage.objects.create(
                        session=session,
                        role='function',
                        content=json.dumps(function_result_message, ensure_ascii=False),
                        function_name=function_name
                    )

                    messages.append(Messages(
                        role=MessagesRole.FUNCTION,
                        content=json.dumps(function_result_message, ensure_ascii=False),
                        name=function_name
                    ))

                    continue

                # Выполняем функцию
                if function_name == "search_products":
                    function_args = self._normalize_search_products_args(user_message, function_args)
                    result = search_products(**function_args)
                    if not result:
                        function_result_message = {"error": "no_products_found", "message": "Товары не найдены..."}
                    else:
                        function_result_message = result
                        found_products.extend(result)
                elif function_name == "get_product_details":
                    product_id = function_args.get('product_id')
                    if not Product.objects.filter(id=product_id).exists():
                        function_result_message = {"error": "product_not_found", "message": f"Товар с ID {product_id} не найден."}
                    else:
                        function_result_message = get_product_details(**function_args)

                elif function_name == "search_web":
                    q = function_args.get("query", "")
                    function_result_message = self._run_search_web(q)

                else:
                    function_result_message = {"error": f"Неизвестная функция: {function_name}"}

                # Сохраняем результат функции в БД
                ChatMessage.objects.create(session=session, role='function', content=json.dumps(function_result_message, ensure_ascii=False), function_name=function_name)
                messages.append(Messages(
                    role=MessagesRole.FUNCTION,
                    content=json.dumps(function_result_message, ensure_ascii=False),
                    name=function_name
                ))
                if (
                    function_name == "get_product_details"
                    and isinstance(function_result_message, dict)
                    and not function_result_message.get("error")
                ):
                    product_name = function_result_message.get("name", "")
                    web_query = f"{product_name} характеристики"

                    web_function_call = FunctionCall(
                        name="search_web",
                        arguments={"query": web_query}
                    )

                    # Сохраняем искусственный вызов search_web в историю
                    ChatMessage.objects.create(
                        session=session,
                        role="assistant",
                        content="",
                        function_name="search_web",
                    )

                    messages.append(Messages(
                        role=MessagesRole.ASSISTANT,
                        content="",
                        function_call=web_function_call
                    ))

                    web_result = self._run_search_web(web_query)

                    ChatMessage.objects.create(
                        session=session,
                        role="function",
                        content=json.dumps(web_result, ensure_ascii=False),
                        function_name="search_web",
                    )

                    messages.append(Messages(
                        role=MessagesRole.FUNCTION,
                        content=json.dumps(web_result, ensure_ascii=False),
                        name="search_web"
                    ))
            else:
                final_answer = "Я не смог быстро сформировать ответ. Попробуйте уточнить запрос или задать его короче."
                ChatMessage.objects.create(session=session, role='assistant', content=final_answer)

            return final_answer, found_products

        except Exception as e:
            import traceback
            print("=== ОШИБКА В CHAT SERVICE ===")
            traceback.print_exc()       # покажет ошибку в терминале
            print("============================")
            logger.exception("process_message_with_products error")
            return "Произошла ошибка. Попробуйте позже.", []

    def process_message(self, user_message: str, session_id: str, chat_history: list = None):
        messages = []
        messages.append(Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT))
        if chat_history:
            for msg in chat_history:
                role = MessagesRole.USER if msg['role'] == 'user' else MessagesRole.ASSISTANT
                messages.append(Messages(role=role, content=msg['content']))
        messages.append(Messages(role=MessagesRole.USER, content=user_message))

        try:
            for _ in range(MAX_TOOL_CALLS):
                response = self.client.chat(Chat(
                    messages=messages,
                    functions=self.functions,
                    function_call="auto"
                ))
                response_message = response.choices[0].message

                # Если модель не хочет вызывать функцию — выходим из цикла
                if not response_message.function_call:
                    final_answer = response_message.content
                    break

                # Сохраняем запрос функции в историю
                messages.append(Messages(
                    role=MessagesRole.ASSISTANT,
                    content="",
                    function_call=response_message.function_call
                ))

                function_name = response_message.function_call.name
                raw_args = response_message.function_call.arguments
                try:
                    if isinstance(raw_args, str):
                        function_args = json.loads(raw_args or "{}")
                    else:
                        function_args = raw_args or {}
                except json.JSONDecodeError:
                    function_args = {}

                if not isinstance(function_args, dict):
                    function_args = {}

                # Выполняем функцию и готовим результат
                if function_name == "search_products":
                    function_args = self._normalize_search_products_args(user_message, function_args)
                    result = search_products(**function_args)
                    if not result:
                        function_result_message = {
                            "error": "no_products_found",
                            "message": "Товары не найдены. Сообщи пользователю, что по заданным критериям ничего нет, и предложи изменить параметры."
                        }
                    else:
                        function_result_message = result
                elif function_name == "get_product_details":
                    product_id = function_args.get('product_id')
                    if not Product.objects.filter(id=product_id).exists():
                        function_result_message = {
                            "error": "product_not_found",
                            "message": f"Товар с ID {product_id} не найден. Сообщи пользователю, что информация отсутствует."
                        }
                    else:
                        function_result_message = get_product_details(**function_args)

                elif function_name == "search_web":
                    q = function_args.get("query", "")
                    function_result_message = self._run_search_web(q)

                else:
                    function_result_message = {"error": f"Неизвестная функция: {function_name}"}

                # Добавляем результат функции в историю
                messages.append(Messages(
                    role=MessagesRole.FUNCTION,
                    content=json.dumps(function_result_message, ensure_ascii=False),
                    name=function_name
                ))

                if (
                    function_name == "get_product_details"
                    and isinstance(function_result_message, dict)
                    and not function_result_message.get("error")
                ):
                    product_name = function_result_message.get("name", "")
                    web_query = f"{product_name} характеристики"

                    web_function_call = FunctionCall(
                        name="search_web",
                        arguments={"query": web_query}
                    )

                    messages.append(Messages(
                        role=MessagesRole.ASSISTANT,
                        content="",
                        function_call=web_function_call
                    ))

                    web_result = self._run_search_web(web_query)

                    messages.append(Messages(
                        role=MessagesRole.FUNCTION,
                        content=json.dumps(web_result, ensure_ascii=False),
                        name="search_web"
                    ))
                # Цикл продолжается — модель может снова вызвать функцию
            else:
                final_answer = "Я не смог быстро сформировать ответ. Попробуйте уточнить запрос или задать его короче."

            return final_answer

        except GigaChatException as e:
            logger.error(f"GigaChat error {e.status_code}: {e}")
            return "Произошла ошибка при обращении к AI-сервису. Попробуйте позже."
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
            logger.error(f"Network error: {e}")
            return "Сервис временно недоступен. Попробуйте позже."
        except Exception as e:
            logger.exception("Unexpected error")
            return "Произошла непредвиденная ошибка. Мы уже работаем над её устранением."
