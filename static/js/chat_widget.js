// chatbot/static/chatbot/js/chat_widget.js

(function() {
    // Дожидаемся полной загрузки DOM
    document.addEventListener('DOMContentLoaded', function() {
        // Элементы DOM
        const toggleBtn = document.getElementById('ai-chat-toggle');
        const chatWindow = document.getElementById('ai-chat-window');
        const closeBtn = document.getElementById('ai-chat-close');
        const chatLog = document.getElementById('ai-chat-log');
        const chatInput = document.getElementById('ai-chat-input');
        const sendBtn = document.getElementById('ai-chat-send');
        const typingIndicator = document.getElementById('ai-typing-indicator');

        let lastInteractedProductId = null;
        lastInteractedInstrumentType = null;

        // Если виджет не найден (например, на странице без base), выходим
        if (!toggleBtn || !chatWindow) return;

        // Идентификатор сессии (подставляется из шаблона)
        const storedSessionId = localStorage.getItem('aiChatSessionId');
        let sessionId = storedSessionId && storedSessionId !== 'anonymous'
            ? storedSessionId
            : (window.AI_CHAT_SESSION_ID || 'anonymous');

        // История диалога (храним на клиенте)
        let chatHistory = [];
        let welcomeShown = false; // флаг, показывали ли уже приветствие

        // Функция для вставки приветствия
        function showWelcomeMessage() {
            const welcomeDiv = document.createElement('div');
            welcomeDiv.classList.add('ai-message', 'assistant');
            welcomeDiv.textContent = '👋 Здравствуйте! Я AI-консультант магазина музыкальных инструментов «Music Store». Могу помочь подобрать инструмент, исходя из вашего уровня подготовки, бюджета и предпочтений. Задайте вопрос!';
            chatLog.appendChild(welcomeDiv);
            chatLog.scrollTop = chatLog.scrollHeight;
            welcomeShown = true;
        }

        // Функция получения CSRF-токена
        function getCookie(name) {
            let cookieValue = null;
            if (document.cookie && document.cookie !== '') {
                const cookies = document.cookie.split(';');
                for (let i = 0; i < cookies.length; i++) {
                    const cookie = cookies[i].trim();
                    if (cookie.substring(0, name.length + 1) === (name + '=')) {
                        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                        break;
                    }
                }
            }
            return cookieValue;
        }

        // Добавление сообщения в лог
        function appendMessage(role, content) {
            const messageDiv = document.createElement('div');
            messageDiv.classList.add('ai-message', role);
            messageDiv.textContent = content;
            chatLog.appendChild(messageDiv);
            chatLog.scrollTop = chatLog.scrollHeight;

            if (role === 'user' || role === 'assistant') {
                chatHistory.push({ role: role, content: content });
            }
        }

        // Индикатор печати
        function setTyping(show) {
            typingIndicator.style.display = show ? 'block' : 'none';
            chatLog.scrollTop = chatLog.scrollHeight;
        }

        // Отправка сообщения
        async function sendMessage() {
            const message = chatInput.value.trim();
            if (!message) return;

            // Формируем сообщение, которое уйдёт на сервер
            let finalMessage = message;

            // Проверяем, спрашивает ли пользователь о последнем показанном товаре
            const productCards = document.querySelectorAll('.ai-product-card[data-product-id]');
            const lastProductCard = productCards.length > 0 
                ? productCards[productCards.length - 1] 
                : null;
            if (lastInteractedProductId && /(эт(?:от|а|у|и)|про него|про нее|о нем|о ней|характеристики)/i.test(message)) {
                if (lastInteractedInstrumentType === 'equipment') {
                    finalMessage = `расскажи подробнее про комбик с ID ${lastInteractedProductId}`;
                } else if (lastInteractedInstrumentType === 'guitar') {
                    finalMessage = `расскажи подробнее про гитару с ID ${lastInteractedProductId}`;
                } else {
                    finalMessage = `расскажи подробнее про товар с ID ${lastInteractedProductId}`;
                }
            }

            // Далее продолжаем как обычно, но отправляем finalMessage
            appendMessage('user', message); // пользователь видит свой оригинальный текст
            chatInput.value = '';
            setTyping(true);
            sendBtn.disabled = true;
            chatInput.disabled = true;

            try {
                const response = await fetch('/chat/api/chat/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCookie('csrftoken')
                    },
                    body: JSON.stringify({
                        message: finalMessage,        // ← обёртка с ID
                        session_id: sessionId,
                        history: chatHistory
                    })
                });

                const data = await response.json();
                if (data.session_id) {
                    sessionId = data.session_id;
                    localStorage.setItem('aiChatSessionId', sessionId);
                }
                if (data.response) {
                    // Создаём контейнер для сообщения ассистента
                    const assistantMsgDiv = document.createElement('div');
                    assistantMsgDiv.classList.add('ai-message', 'assistant');
                    
                    // Вставляем текст ответа (с переносами)
                    const textSpan = document.createElement('span');
                    textSpan.textContent = data.response;
                    assistantMsgDiv.appendChild(textSpan);
                    
                    // Если есть товары, добавляем карточки
                    if (data.products && data.products.length > 0) {
                        const uniqueProducts = [];
                        const seenIds = new Set();
                        for (const p of data.products) {
                            if (!seenIds.has(p.id)) {
                                seenIds.add(p.id);
                                uniqueProducts.push(p);
                            }
                        }
                        const productsContainer = document.createElement('div');
                        productsContainer.className = 'ai-products-container';
                        uniqueProducts.forEach(product => {
                            const card = createProductCard(product);
                            productsContainer.appendChild(card);
                        });
                        assistantMsgDiv.appendChild(productsContainer);
                    }
                    
                    chatLog.appendChild(assistantMsgDiv);
                    chatLog.scrollTop = chatLog.scrollHeight;

                    // Сохраняем в историю только текст ответа
                    chatHistory.push({ role: 'assistant', content: data.response });
                } else {
                    appendMessage('assistant', 'Извините, произошла ошибка. Попробуйте позже.');
                }
            } catch (error) {
                console.error('Ошибка чата:', error);
                appendMessage('assistant', 'Ошибка сети. Проверьте подключение.');
            } finally {
                setTyping(false);
                sendBtn.disabled = false;
                chatInput.disabled = false;
                chatInput.focus();
            }
        }

        // Функция создания HTML-карточки товара
        function createProductCard(product) {
            const card = document.createElement('div');
            card.className = 'ai-product-card';
            card.onclick = () => { window.location.href = product.url; };
            card.dataset.productId = product.id;
            card.dataset.instrumentType = product.instrument_type || '';

            // Изображение (если есть в данных)
            if (product.image) {
                const img = document.createElement('img');
                img.src = product.image;
                img.alt = product.name;
                img.className = 'ai-product-image';
                card.appendChild(img);
            }

            card.onclick = (e) => {
                e.stopPropagation();
                lastInteractedProductId = product.id;
                lastInteractedInstrumentType = product.instrument_type;
                window.location.href = product.url;
            };
            
            const info = document.createElement('div');
            info.className = 'ai-product-info';
            
            const name = document.createElement('div');
            name.className = 'ai-product-name';
            name.textContent = product.name;
            
            const price = document.createElement('div');
            price.className = 'ai-product-price';
            price.textContent = `${product.price} руб.`;
            
            info.appendChild(name);
            info.appendChild(price);
            card.appendChild(info);
            
            return card;
        }

        // Загрузка истории с сервера
        async function loadHistory() {
            try {
                const response = await fetch(`/chat/api/chat/history/?session_id=${encodeURIComponent(sessionId)}`);
                const data = await response.json();
                // Очищаем лог и историю
                chatLog.innerHTML = '';
                chatHistory = [];
                // Восстанавливаем текстовые сообщения
                if (data.history && data.history.length > 0) {
                    data.history.forEach(msg => {
                        appendMessage(msg.role, msg.content);
                    });
                } else {
                    if (!welcomeShown) showWelcomeMessage();
                }
                // Если сервер вернул товары из последнего ответа – рисуем карточки
                if (data.products && data.products.length > 0) {
                    const uniqueProducts = [];
                    const seenIds = new Set();
                    for (const p of data.products) {
                        if (!seenIds.has(p.id)) {
                            seenIds.add(p.id);
                            uniqueProducts.push(p);
                        }
                    }
                    const productsContainer = document.createElement('div');
                    productsContainer.className = 'ai-products-container';
                    uniqueProducts.forEach(product => {
                        const card = createProductCard(product);
                        productsContainer.appendChild(card);
                    });
                    // Добавляем карточки в конец чат-лога (не в последнее сообщение)
                    chatLog.appendChild(productsContainer);
                    chatLog.scrollTop = chatLog.scrollHeight;
                }
            } catch (e) {
                console.error('Failed to load history', e);
                if (!welcomeShown) showWelcomeMessage();
            }
        }

        // Единая функция переключения чата
        function toggleChat() {
            if (chatWindow.style.display === 'none') {
                chatWindow.style.display = 'flex';
                chatInput.focus();
                if (chatLog.children.length === 0) {
                    loadHistory();
                }
            } else {
                chatWindow.style.display = 'none';
            }
        }

        // Обработчики событий
        toggleBtn.addEventListener('click', toggleChat);
        closeBtn.addEventListener('click', function() {
            chatWindow.style.display = 'none';
        });
        sendBtn.addEventListener('click', sendMessage);
        chatInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                sendMessage();
            }
        });

        // Закрытие по клику вне окна
        document.addEventListener('click', function(e) {
            if (!chatWindow.contains(e.target) && e.target !== toggleBtn && !toggleBtn.contains(e.target)) {
                chatWindow.style.display = 'none';
            }
        });
    });
})();
