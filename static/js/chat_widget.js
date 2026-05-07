// static/js/chat_widget.js

(function() {
    document.addEventListener('DOMContentLoaded', function() {
        const toggleBtn = document.getElementById('ai-chat-toggle');
        const chatWindow = document.getElementById('ai-chat-window');
        const closeBtn = document.getElementById('ai-chat-close');
        const chatLog = document.getElementById('ai-chat-log');
        const chatInput = document.getElementById('ai-chat-input');
        const sendBtn = document.getElementById('ai-chat-send');
        const typingIndicator = document.getElementById('ai-typing-indicator');

        let lastInteractedProductId = null;
        let lastInteractedInstrumentType = null;

        if (!toggleBtn || !chatWindow) return;

        // Session ID
        let sessionId = window.AI_CHAT_SESSION_ID || 'anonymous';
        let isAuthenticated = Boolean(window.AI_CHAT_AUTHENTICATED);

        // Восстанавливаем sessionId для анонимов из sessionStorage
        let storedSessionId = sessionStorage.getItem('aiChatSessionId');
        if (storedSessionId) {
            sessionId = storedSessionId;
        } else {
            sessionStorage.setItem('aiChatSessionId', sessionId);
        }

        let chatHistory = [];
        let lastProducts = [];
        let welcomeShown = false;

        // Очистка при полной перезагрузке для анонимов
        const navEntry = performance.getEntriesByType('navigation')[0];
        if (!isAuthenticated && navEntry && navEntry.type === 'reload') {
            sessionStorage.removeItem('aiChatHistory');
            sessionStorage.removeItem('aiChatProducts');
        }

        function showWelcomeMessage() {
            const welcomeDiv = document.createElement('div');
            welcomeDiv.classList.add('ai-message', 'assistant');
            welcomeDiv.textContent = '👋 Здравствуйте! Я AI-консультант магазина музыкальных инструментов «Music Store». Могу помочь подобрать инструмент, исходя из вашего уровня подготовки, бюджета и предпочтений. Задайте вопрос!';
            chatLog.appendChild(welcomeDiv);
            chatLog.scrollTop = chatLog.scrollHeight;
            welcomeShown = true;
        }

        function saveAnonymousState() {
            if (isAuthenticated) return;
            sessionStorage.setItem('aiChatHistory', JSON.stringify(chatHistory));
            sessionStorage.setItem('aiChatProducts', JSON.stringify(lastProducts));
        }

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

        function appendMessage(role, content) {
            const messageDiv = document.createElement('div');
            messageDiv.classList.add('ai-message', role);
            messageDiv.textContent = content;
            chatLog.appendChild(messageDiv);
            chatLog.scrollTop = chatLog.scrollHeight;

            if (role === 'user' || role === 'assistant') {
                chatHistory.push({ role: role, content: content });
                saveAnonymousState();
            }
        }

        function setTyping(show) {
            typingIndicator.style.display = show ? 'block' : 'none';
            chatLog.scrollTop = chatLog.scrollHeight;
        }

        async function sendMessage() {
            const message = chatInput.value.trim();
            if (!message) return;

            // Фallback для lastInteractedProductId
            if (!lastInteractedProductId && lastProducts.length > 0) {
                lastInteractedProductId = lastProducts[0].id;
                lastInteractedInstrumentType = lastProducts[0].instrument_type || '';
            }

            const historyToSend = chatHistory.slice();
            let finalMessage = message;

            if (lastInteractedProductId && /(эт(?:от|а|у|и)|про него|про нее|о нем|о ней|характеристики)/i.test(message)) {
                if (lastInteractedInstrumentType === 'equipment') {
                    finalMessage = `расскажи подробнее про комбик с ID ${lastInteractedProductId}`;
                } else if (lastInteractedInstrumentType === 'guitar') {
                    finalMessage = `расскажи подробнее про гитару с ID ${lastInteractedProductId}`;
                } else {
                    finalMessage = `расскажи подробнее про товар с ID ${lastInteractedProductId}`;
                }
            }

            appendMessage('user', message);
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
                        message: finalMessage,
                        session_id: sessionId,
                        history: historyToSend
                    })
                });

                const data = await response.json();
                if (data.session_id) sessionId = data.session_id;
                if (typeof data.authenticated !== 'undefined') isAuthenticated = Boolean(data.authenticated);

                if (data.response) {
                    const assistantMsgDiv = document.createElement('div');
                    assistantMsgDiv.classList.add('ai-message', 'assistant');

                    const textSpan = document.createElement('span');
                    textSpan.textContent = data.response;
                    assistantMsgDiv.appendChild(textSpan);

                    if (data.products && data.products.length > 0) {
                        renderProductCards(data.products, assistantMsgDiv);
                    }

                    chatLog.appendChild(assistantMsgDiv);
                    chatLog.scrollTop = chatLog.scrollHeight;

                    chatHistory.push({ role: 'assistant', content: data.response });
                    saveAnonymousState();
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

        function createProductCard(product) {
            const card = document.createElement('div');
            card.className = 'ai-product-card';
            card.dataset.productId = product.id;
            card.dataset.instrumentType = product.instrument_type || '';

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

        function renderProductCards(products, parentElement) {
            const existingContainer = parentElement.querySelector('.ai-products-container');
            if (existingContainer) existingContainer.remove();
            if (!products || products.length === 0) return;

            const uniqueProducts = [];
            const seenIds = new Set();
            for (const p of products) {
                if (!seenIds.has(p.id)) {
                    seenIds.add(p.id);
                    uniqueProducts.push(p);
                }
            }

            lastProducts = uniqueProducts;
            if (uniqueProducts.length > 0) {
                lastInteractedProductId = uniqueProducts[0].id;
                lastInteractedInstrumentType = uniqueProducts[0].instrument_type || '';
            }

            saveAnonymousState();

            const productsContainer = document.createElement('div');
            productsContainer.className = 'ai-products-container';
            uniqueProducts.forEach(product => {
                productsContainer.appendChild(createProductCard(product));
            });

            parentElement.appendChild(productsContainer);
            chatLog.scrollTop = chatLog.scrollHeight;
        }

        async function loadHistory() {
            if (!isAuthenticated) {
                chatLog.innerHTML = '';
                chatHistory = [];
                lastProducts = [];

                const savedHistory = sessionStorage.getItem('aiChatHistory');
                const savedProducts = sessionStorage.getItem('aiChatProducts');

                if (savedHistory) {
                    try {
                        JSON.parse(savedHistory).forEach(msg => appendMessage(msg.role, msg.content));
                    } catch {}
                }

                if (savedProducts) {
                    try {
                        lastProducts = JSON.parse(savedProducts);
                    } catch {}
                    if (lastProducts.length > 0) {
                        renderProductCards(lastProducts, chatLog);
                    }
                }

                if (!chatHistory.some(msg => msg.role === 'assistant') && !welcomeShown) showWelcomeMessage();
                return;
            }

            try {
                const response = await fetch(`/chat/api/chat/history/?session_id=${encodeURIComponent(sessionId)}`);
                const data = await response.json();
                chatLog.innerHTML = '';
                chatHistory = [];

                if (data.history && data.history.length > 0) {
                    data.history.forEach(msg => appendMessage(msg.role, msg.content));
                } else if (!welcomeShown) {
                    showWelcomeMessage();
                }

                if (data.products && data.products.length > 0) {
                    renderProductCards(data.products, chatLog);
                }

            } catch (e) {
                console.error('Failed to load history', e);
                if (!welcomeShown) showWelcomeMessage();
            }
        }

        function toggleChat() {
            if (chatWindow.style.display === 'none') {
                chatWindow.style.display = 'flex';
                chatInput.focus();
                if (chatLog.children.length === 0) loadHistory();
            } else {
                chatWindow.style.display = 'none';
            }
        }

        toggleBtn.addEventListener('click', toggleChat);
        closeBtn.addEventListener('click', () => { chatWindow.style.display = 'none'; });
        sendBtn.addEventListener('click', sendMessage);
        chatInput.addEventListener('keypress', e => { if (e.key === 'Enter') sendMessage(); });

        document.addEventListener('click', e => {
            if (!chatWindow.contains(e.target) && e.target !== toggleBtn && !toggleBtn.contains(e.target)) {
                chatWindow.style.display = 'none';
            }
        });
    });
})();