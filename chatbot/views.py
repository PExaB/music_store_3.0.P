import json
import traceback
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .gigachat_service import GigaChatService
import re
from .models import ChatSession, ChatMessage


def clean_response(text):
    phrases_to_remove = [
        r'\.?\s*К сожалению, (?:в (?:нашем )?каталоге|детальной информации о характеристиках в нашем каталоге) нет[^.]*\.?',
        r'\.?\s*Характеристики не указаны в каталоге\.*',
        r'\.?\s*В каталоге: не указана[^.]*\.?',
        r'\.?\s*В каталоге: [^.]*\.?',
        r'\.?\s*Провожу дополнительный поиск[^.]*\.?',
        r'\.?\s*\(выполняется поиск в интернете\)',
        r'\.?\s*\(выполняю `search_web`[^)]*\)',
        r'\.?\s*По данным из интернета:\s*',
        r'\.?\s*Источник: [^.]*\.?',
        r'\.?\s*В базе не указано[^.]*\.?',
        r'\.?\s*…\s*\(выполняю[^)]*\)',
        r'\.?\s*Поиск в интернете:',
        r'\.?\s*Информация из интернета:',
    ]
    for phrase in phrases_to_remove:
        text = re.sub(phrase, '', text, flags=re.IGNORECASE)
    # Убираем лишние точки и пробелы
    text = re.sub(r'\.{2,}', '.', text)
    text = re.sub(r'\n\s*\n', '\n', text).strip()
    return text

@csrf_exempt
def chat_api(request):
    if request.method == 'POST':
        try:
            # Создаём сессию, если её ещё нет
            if not request.session.session_key:
                request.session.create()
            data = json.loads(request.body)
            user_message = data.get('message', '').strip()
            incoming_session_id = data.get('session_id')
            session_id = incoming_session_id if incoming_session_id and incoming_session_id != 'anonymous' else request.session.session_key
            chat_history = data.get('history', [])

            if not user_message:
                return JsonResponse({'error': 'Сообщение не может быть пустым'}, status=400)

            service = GigaChatService()
            response_text, products = service.process_message_with_products(user_message, session_id, chat_history)

            # Получаем сессию чата (она уже создана внутри process_message_with_products)
            try:
                chat_session = ChatSession.objects.get(session_key=session_id)
            except ChatSession.DoesNotExist:
                # Если по какой-то причине не создана - создаём
                chat_session = ChatSession.objects.create(session_key=session_id)

            # Сохраняем товары
            if products:
                chat_session.last_products = json.dumps(products, ensure_ascii=False)
            chat_session.save()

            return JsonResponse({
                'response': clean_response(response_text),
                'products': products,
                'session_id': session_id
            })
        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'error': f'Server error: {str(e)}'}, status=500)
    return JsonResponse({'error': 'Method not allowed'}, status=405)

def get_chat_history(request):
    session_id = request.GET.get('session_id', 'anonymous')
    try:
        session = ChatSession.objects.get(session_key=session_id)
    except ChatSession.DoesNotExist:
        return JsonResponse({'history': [], 'products': []})
    messages = ChatMessage.objects.filter(session=session).order_by('created_at')
    history = [{'role': m.role, 'content': m.content} for m in messages if m.role in ('user', 'assistant')]
    products = []
    if session.last_products:
        try:
            products = json.loads(session.last_products)
        except:
            pass
    return JsonResponse({'history': history, 'products': products})
