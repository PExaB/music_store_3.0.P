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
            if not request.session.session_key:
                request.session.create()

            data = json.loads(request.body)
            user_message = data.get('message', '').strip()
            chat_history = data.get('history', [])

            if not user_message:
                return JsonResponse({'error': 'Сообщение не может быть пустым'}, status=400)

            is_auth = request.user.is_authenticated

            if is_auth:
                # История привязана к пользователю, а не к localStorage
                session_id = f"user_{request.user.id}"
            else:
                # Для анонимных используем временный id, но не сохраняем историю
                session_id = f"anon_{request.session.session_key}"

                # На всякий случай удаляем старую анонимную историю
                ChatSession.objects.filter(session_key=session_id).delete()

            service = GigaChatService()
            response_text, products = service.process_message_with_products(
                user_message,
                session_id,
                chat_history
            )

            if is_auth:
                chat_session, _ = ChatSession.objects.get_or_create(
                    session_key=session_id,
                    defaults={'user': request.user}
                )

                if chat_session.user_id != request.user.id:
                    chat_session.user = request.user

                if products:
                    chat_session.last_products = json.dumps(products, ensure_ascii=False)

                chat_session.save()
            else:
                # Удаляем всё, что service успел сохранить для анонима
                ChatSession.objects.filter(session_key=session_id).delete()

            return JsonResponse({
                'response': clean_response(response_text),
                'products': products,
                'session_id': session_id if is_auth else 'anonymous',
                'authenticated': is_auth,
            })

        except Exception as e:
            traceback.print_exc()
            return JsonResponse({'error': f'Server error: {str(e)}'}, status=500)

    return JsonResponse({'error': 'Method not allowed'}, status=405)


def get_chat_history(request):
    if not request.user.is_authenticated:
        return JsonResponse({
            'history': [],
            'products': [],
            'authenticated': False,
        })

    session_id = f"user_{request.user.id}"

    try:
        session = ChatSession.objects.get(
            session_key=session_id,
            user=request.user
        )
    except ChatSession.DoesNotExist:
        return JsonResponse({
            'history': [],
            'products': [],
            'authenticated': True,
        })

    messages = ChatMessage.objects.filter(session=session).order_by('created_at')

    history = [
        {'role': m.role, 'content': m.content}
        for m in messages
        if m.role in ('user', 'assistant') and m.content
    ]

    products = []
    if session.last_products:
        try:
            products = json.loads(session.last_products)
        except Exception:
            pass

    return JsonResponse({
        'history': history,
        'products': products,
        'authenticated': True,
    })
