# store/gigachat_service.py
import json
import logging
from django.conf import settings
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole, Function
from gigachat.exceptions import GigaChatException
import httpx  # вместо requests
from store.models import Product
import urllib.request
import urllib.parse
from .models import ChatSession, ChatMessage

from .product_utils import search_products, get_product_details

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — AI-консультант магазина «Music Store». Твоя задача — быстро подбирать реальные товары из каталога и предоставлять исчерпывающую информацию, комбинируя данные из базы магазина и интернета.

**Жёсткие правила:**
1. **Никогда не выдумывай** названия, бренды, цены или характеристики. Используй только данные из `search_products`, `get_product_details` и `search_web`. Если данных нет ни в базе, ни в интернете, честно сообщи об этом.
2. Если `search_products` возвращает пустой список, скажи: «По вашему запросу ничего не найдено. Попробуйте изменить бюджет или другие параметры». **Не предлагай вымышленных моделей.**
3. **Никогда не ищи «готовые комплекты».** В базе их нет. Твоя задача — подбирать товары по отдельности.
4. **Запросы по бренду**: если пользователь спрашивает о товарах конкретного бренда (например, «Fender», «Yamaha»), немедленно вызывай `search_products(query="название_бренда")` без дополнительных фильтров. Если пользователь уточняет тип инструмента (например, «гитары Fender»), добавь соответствующий параметр (`instrument_type='guitar'`).

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

**Ответы на запросы о характеристиках и деталях — действуй решительно:**
- Если пользователь явно просит «подробнее», «расскажи характеристики», «какая мощность», «материал», «особенности» и т.п. И при этом в сообщении или истории есть конкретный товар (по ID или названию):
  1. **Сразу вызывай `get_product_details`** с ID этого товара.
  2. **Немедленно после этого вызывай `search_web`** с запросом, содержащим полное название товара и конкретную характеристику (например, «Marshall CODE 25 мощность»). **Не смотри на поле `features`** – вызывай поиск в любом случае.
- Если `get_product_details` вернул заполненное `features`, всё равно дополни его информацией из интернета и выдай пользователю объединённый ответ.
- Если `search_web` не дал результатов, используй только данные из `get_product_details` и честно скажи: «Дополнительных сведений в интернете не найдено.»
- **Никогда не спрашивай: «Хотите, чтобы я поискал?»**. Просто делай.

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

        # Добавляем текущее сообщение пользователя
        messages.append(Messages(role=MessagesRole.USER, content=user_message))
        # Сохраняем сообщение пользователя в БД
        ChatMessage.objects.create(session=session, role='user', content=user_message)

        final_answer = ""
        found_products = []

        try:
            while True:
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
                if isinstance(raw_args, str):
                    function_args = json.loads(raw_args)
                else:
                    function_args = raw_args

                # Выполняем функцию
                if function_name == "search_products":
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
                    q = function_args.get('query', '')
                    try:
                        encoded_query = urllib.parse.quote(q)
                        search_url = f"https://ru.wikipedia.org/w/api.php?action=query&list=search&srsearch={encoded_query}&format=json&srlimit=1"
                        req = urllib.request.Request(search_url, headers={'User-Agent': 'MusicStoreBot/1.0'})
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            data = json.loads(resp.read())
                        search_results = data.get("query", {}).get("search", [])
                        if search_results:
                            page_id = search_results[0]["pageid"]
                            desc_url = f"https://ru.wikipedia.org/w/api.php?action=query&prop=extracts&exintro=True&explaintext=True&pageids={page_id}&format=json"
                            desc_req = urllib.request.Request(desc_url, headers={'User-Agent': 'MusicStoreBot/1.0'})
                            with urllib.request.urlopen(desc_req, timeout=10) as desc_resp:
                                desc_data = json.loads(desc_resp.read())
                            pages = desc_data.get("query", {}).get("pages", {})
                            extract = next(iter(pages.values())).get("extract", "Информация не найдена.")
                            function_result_message = {"query": q, "snippet": extract[:500]}
                        else:
                            function_result_message = {"query": q, "snippet": "Информация не найдена."}
                    except Exception as e:
                        logger.error(f"Search web error: {e}")
                        function_result_message = {"query": q, "error": f"Не удалось выполнить поиск: {str(e)}"}
                    print(f">>> search_web returned: {function_result_message}")
                    
                else:
                    function_result_message = {"error": f"Неизвестная функция: {function_name}"}

                # Сохраняем результат функции в БД
                ChatMessage.objects.create(session=session, role='function', content=json.dumps(function_result_message, ensure_ascii=False), function_name=function_name)
                messages.append(Messages(
                    role=MessagesRole.FUNCTION,
                    content=json.dumps(function_result_message, ensure_ascii=False),
                    name=function_name
                ))

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
            while True:
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
                if isinstance(raw_args, str):
                    function_args = json.loads(raw_args)
                else:
                    function_args = raw_args

                # Выполняем функцию и готовим результат
                if function_name == "search_products":
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
                    q = function_args.get('query', '')
                    try:
                        encoded_query = urllib.parse.quote(q)
                        search_url = f"https://ru.wikipedia.org/w/api.php?action=query&list=search&srsearch={encoded_query}&format=json&srlimit=1"
                        req = urllib.request.Request(search_url, headers={'User-Agent': 'MusicStoreBot/1.0'})
                        with urllib.request.urlopen(req, timeout=10) as desc_resp:
                            data = json.loads(desc_resp.read())
                        
                        search_results = data.get("query", {}).get("search", [])
                        if search_results:
                            page_id = search_results[0]["pageid"]
                            desc_url = f"https://ru.wikipedia.org/w/api.php?action=query&prop=extracts&exintro=True&explaintext=True&pageids={page_id}&format=json"
                            with urllib.request.urlopen(desc_url, timeout=10) as desc_resp:
                                desc_data = json.loads(desc_resp.read())
                            pages = desc_data.get("query", {}).get("pages", {})
                            extract = next(iter(pages.values())).get("extract", "Информация не найдена.")
                            function_result_message = {"query": q, "snippet": extract[:500]}
                        else:
                            function_result_message = {"query": q, "snippet": "Информация не найдена."}
                    except Exception as e:
                        function_result_message = {"error": f"Ошибка поиска: {str(e)}"}

                else:
                    function_result_message = {"error": f"Неизвестная функция: {function_name}"}

                # Добавляем результат функции в историю
                messages.append(Messages(
                    role=MessagesRole.FUNCTION,
                    content=json.dumps(function_result_message, ensure_ascii=False),
                    name=function_name
                ))
                # Цикл продолжается — модель может снова вызвать функцию

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