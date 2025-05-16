const tg = window.Telegram.WebApp;
tg.expand(); // Раскрываем Web App на весь экран

const messages = document.getElementById('messages');
const form = document.getElementById('form');
const input = document.getElementById('input');
const buttonContainer = document.getElementById('button-container');
const satisfiedBtn = document.getElementById('satisfied-btn');
const operatorBtn = document.getElementById('operator-btn');
const newChatButtonContainer = document.getElementById('new-chat-button-container');
const newChatBtn = document.getElementById('new-chat-btn');
const fileInput = document.getElementById('file-input');
const uploadBtn = document.getElementById('upload-btn');
const keyboardToggle = document.getElementById('keyboard-toggle');

let ws = null;
let currentChatId = null;
let userId = null;
let userName = null;
let selectedFile = null;
let isKeyboardVisible = false;
let keyboardTimeout;
let inputTimeout;
let lastWindowHeight = window.innerHeight;
let lastSentMessage = null;
let isSubmitting = false; // Флаг для предотвращения повторных отправок

function addMessage(senderType, text, timestamp = new Date().toISOString(), senderId = '', media = null) {
    // Если нет ни текста, ни медиа, не создаем сообщение
    if (!text && !media) return;

    const item = document.createElement('li');
    item.classList.add(senderType);

    // Добавляем медиа-контент, если он есть
    if (media) {
        const mediaContainer = document.createElement('div');
        mediaContainer.classList.add('media-container');
        
        // Используем file_id как есть
        const filePath = media.file_id;
        
        switch (media.type) {
            case 'photo':
                const img = document.createElement('img');
                img.src = `/api/media/${filePath}`;
                img.alt = media.caption || 'Фото';
                img.loading = 'lazy';
                img.style.cursor = 'pointer';
                img.addEventListener('click', () => openMediaModal(media));
                // Добавляем обработчик загрузки изображения
                img.onload = () => scrollToLastMessage();
                mediaContainer.appendChild(img);
                break;
            case 'video':
                const video = document.createElement('video');
                video.src = `/api/media/${filePath}`;
                video.controls = true;
                video.preload = 'metadata';
                video.style.cursor = 'default';
                mediaContainer.appendChild(video);
                break;
            case 'voice':
                const audio = document.createElement('audio');
                audio.src = `/api/media/${filePath}`;
                audio.controls = true;
                audio.preload = 'metadata';
                audio.addEventListener('click', () => openMediaModal(media));
                mediaContainer.appendChild(audio);
                break;
            case 'video_note':
                const videoNote = document.createElement('video');
                videoNote.src = `/api/media/${filePath}`;
                videoNote.controls = true;
                videoNote.preload = 'metadata';
                videoNote.style.maxWidth = '200px';
                videoNote.style.maxHeight = '200px';
                videoNote.style.cursor = 'default';
                mediaContainer.appendChild(videoNote);
                break;
            case 'document':
                const docLink = document.createElement('a');
                docLink.href = `/api/media/${filePath}`;
                docLink.textContent = media.caption || '📄 Документ';
                docLink.target = '_blank';
                docLink.style.display = 'block';
                docLink.style.margin = '5px 0';
                docLink.style.cursor = 'pointer';
                docLink.addEventListener('click', (e) => {
                    e.preventDefault();
                    openMediaModal(media);
                });
                mediaContainer.appendChild(docLink);
                break;
        }

        // Добавляем подпись к медиа, если есть текст
        if (text && media.type !== 'document') {
            const caption = document.createElement('div');
            caption.classList.add('media-caption');
            caption.textContent = text;
            mediaContainer.appendChild(caption);
        }

        item.appendChild(mediaContainer);
    } else if (text) {
        // Добавляем текст только если нет медиа
        const textNode = document.createElement('span');
        textNode.textContent = text;
        item.appendChild(textNode);
    }

    const timeNode = document.createElement('small');
    const date = new Date(timestamp);
    timeNode.textContent = `${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
    if (senderId) {
        timeNode.textContent += ` (${senderId})`;
    }
    item.appendChild(timeNode);

    messages.appendChild(item);
    scrollToLastMessage();
}

function showButtons(show = true) {
    buttonContainer.style.display = show ? 'block' : 'none';
}

function showNewChatButton(show = true) {
    newChatButtonContainer.style.display = show ? 'block' : 'none';
    form.style.display = show ? 'none' : 'flex'; // Скрываем форму ввода, если показываем кнопку "Начать новый чат"
}

function connectWebSocket() {
    // Получаем user_id и user_name из Telegram Web App InitData
    if (tg.initDataUnsafe && tg.initDataUnsafe.user) {
        userId = tg.initDataUnsafe.user.id;
        userName = tg.initDataUnsafe.user.first_name;
        console.log("User ID:", userId, "User Name:", userName);
    } else {
        console.error("Не удалось получить данные пользователя из Telegram Web App");
        addMessage('system', 'Ошибка: Не удалось получить ID пользователя. Попробуйте перезапустить чат из Telegram.');
        return;
    }

    // Формируем URL для WebSocket с InitData
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws?initData=${encodeURIComponent(tg.initData)}`;
    console.log("Connecting to WebSocket:", wsUrl);

    ws = new WebSocket(wsUrl);

    ws.onopen = function(event) {
        console.log("WebSocket connection opened");
        addMessage('system', 'Соединение установлено.');
        // Запрашиваем историю или статус при подключении (сервер отправит init)
    };

    ws.onmessage = function(event) {
        console.log("Message from server:", event.data);
        try {
            const data = JSON.parse(event.data);
            handleServerMessage(data);
        } catch (e) {
            console.error("Failed to parse message or handle:", e);
            addMessage('system', 'Ошибка обработки сообщения от сервера.');
        }
    };

    ws.onerror = function(event) {
        console.error("WebSocket error observed:", event);
        addMessage('system', 'Ошибка соединения. Попробуйте обновить страницу.');
    };

    ws.onclose = function(event) {
        console.log("WebSocket connection closed:", event);
        addMessage('system', `Соединение закрыто (код: ${event.code}). ${event.reason || ''} Попробуйте обновить страницу.`);
        ws = null; // Сбросить объект WebSocket
        showButtons(false);
        showNewChatButton(false);
        form.style.display = 'none'; // Скрыть форму ввода при разрыве
    };
}

function handleServerMessage(data) {
    if (!data || !data.type || !data.payload) {
        console.warn("Received invalid message structure:", data);
        return;
    }
    const payload = data.payload;

    // Всегда обновляем currentChatId, если он пришел в сообщении
    if (payload.chat_id) {
        currentChatId = payload.chat_id;
        console.log("Current Chat ID updated:", currentChatId);
    }

    switch (data.type) {
        case 'init':
            messages.innerHTML = ''; // Очищаем старые сообщения
            addMessage('system', 'Начните диалог, отправив сообщение. Наш робот постарается ответить на ваш вопрос. Если вы будете не удовлетворены ответом, всегда можно позвать оператора.');
            if (payload.history && payload.history.length > 0) {
                // Отключаем плавную прокрутку для загрузки истории
                payload.history.forEach((msg, index) => {
                    let senderType = 'system'; // По умолчанию
                    if (String(msg.sender_id) === String(userId)) {
                        senderType = 'client';
                    } else if (msg.sender_id === 'ai') {
                        senderType = 'ai';
                    } else if (String(msg.sender_id).match(/^\d+$/)) {
                        senderType = 'manager';
                    }
                    // Используем плавную прокрутку только для последнего сообщения
                    addMessage(senderType, msg.text, msg.timestamp, msg.sender_id, msg.media);
                });
            }
            showButtons(payload.show_buttons || false);
            showNewChatButton(false);
            form.style.display = 'flex';
            input.disabled = false;
            // Убеждаемся, что последнее сообщение видно
            setTimeout(() => scrollToLastMessage(true), 100);
            break;
        case 'message':
            // Определяем тип отправителя
            let senderType;
            if (String(payload.sender_id) === String(userId)) {
                senderType = 'client';
            } else if (payload.sender_id === 'ai') {
                senderType = 'ai';
            } else if (String(payload.sender_id).match(/^\d+$/)) {
                senderType = 'manager';
            } else {
                senderType = 'system';
            }
            
            // Проверяем, не является ли это дубликатом файла
            if (payload.media && lastSentMessage && payload.media.file_id === `${currentChatId}/${selectedFile?.name}`) {
                console.log("Пропускаем дубликат сообщения с файлом");
                return;
            }
            
            addMessage(senderType, payload.text, payload.timestamp, payload.sender_id, payload.media);
            break;
        case 'ai_response':
            addMessage('ai', payload.text, payload.timestamp, 'AI');
            if (payload.show_buttons) {
                showButtons(true);
            }
            break;
        case 'status_update':
            addMessage('system', payload.message);
            if (payload.new_chat_id) {
                currentChatId = payload.new_chat_id;
                console.log("Switched to new chat ID:", currentChatId);
            }
            if (payload.status === 'closed') {
                showButtons(false);
                showNewChatButton(payload.show_new_chat_button);
                input.disabled = true;
            }
            break;
        case 'error':
            addMessage('system', `Ошибка: ${payload.message}`);
            if (payload.show_operator_button) {
                showButtons(true);
                satisfiedBtn.style.display = 'none';
            }
            if (payload.show_new_chat_button) {
                showNewChatButton(true);
            }
            break;
        default:
            console.warn("Unknown message type:", data.type);
    }
}

// --- Обработчики событий ---

// Добавляем обработчик для кнопки загрузки файла
uploadBtn.addEventListener('click', function() {
    fileInput.click();
});

// Добавляем обработчик для drag-and-drop
form.addEventListener('dragover', function(e) {
    e.preventDefault();
    e.stopPropagation();
    form.style.border = '2px dashed #0088cc';
});

form.addEventListener('dragleave', function(e) {
    e.preventDefault();
    e.stopPropagation();
    form.style.border = 'none';
});

form.addEventListener('drop', function(e) {
    e.preventDefault();
    e.stopPropagation();
    form.style.border = 'none';
    
    if (e.dataTransfer.files.length > 0) {
        selectedFile = e.dataTransfer.files[0];
        uploadBtn.title = `Файл: ${selectedFile.name}`;
        // Автоматически отправляем файл
        form.dispatchEvent(new Event('submit'));
    }
});

// Улучшаем обработчик выбора файла
fileInput.addEventListener('change', function(e) {
    if (e.target.files.length > 0) {
        selectedFile = e.target.files[0];
        // Проверяем размер файла
        if (selectedFile.size > 250 * 1024 * 1024) { // 250MB
            addMessage('system', 'Файл слишком большой. Максимальный размер: 250MB');
            selectedFile = null;
            fileInput.value = '';
            uploadBtn.title = 'Прикрепить файл';
            return;
        }
        // Проверяем тип файла
        const allowedTypes = [
            'image/jpeg', 'image/png', 'image/gif', 
            'application/pdf', 'application/msword', 
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.ms-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'text/plain',
            'video/quicktime',
            'video/mp4'
        ];
        if (!allowedTypes.includes(selectedFile.type)) {
            addMessage('system', 'Неподдерживаемый тип файла');
            selectedFile = null;
            fileInput.value = '';
            uploadBtn.title = 'Прикрепить файл';
            return;
        }
        uploadBtn.title = `Файл: ${selectedFile.name}`;
        // Автоматически отправляем файл
        form.dispatchEvent(new Event('submit'));
    }
});

// Добавляем обработчик input для мобильных устройств
fileInput.addEventListener('input', function(e) {
    if (e.target.files.length > 0) {
        selectedFile = e.target.files[0];
        uploadBtn.title = `Файл: ${selectedFile.name}`;
        // Автоматически отправляем файл
        form.dispatchEvent(new Event('submit'));
    }
});

// Модифицируем обработчик отправки формы
form.addEventListener('submit', function(e) {
    e.preventDefault();
    if (isSubmitting) {
        console.log('Отправка уже в процессе, пропускаем');
        return;
    }
    isSubmitting = true;

    if (isKeyboardVisible) {
        input.blur();
        keyboardToggle.classList.remove('visible');
        keyboardToggle.classList.remove('active');
        isKeyboardVisible = false;
    }
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        addMessage('system', 'Нет соединения с сервером.');
        isSubmitting = false;
        return;
    }

    if ((input.value || selectedFile) && !input.disabled) {
        const message = {
            type: 'message',
            payload: {
                text: input.value || '',
                file: selectedFile ? {
                    name: selectedFile.name,
                    type: selectedFile.type,
                    size: selectedFile.size
                } : null
            }
        };

        if (selectedFile) {
            if (!currentChatId) {
                addMessage('system', 'Ошибка: В данный момент нельзя отправлять файлы. Вы сможете это сделать, позвав оператора.');
                isSubmitting = false;
                return;
            }

            const formData = new FormData();
            formData.append('file', selectedFile);
            const uploadUrl = `/upload?chat_id=${currentChatId}&message=${encodeURIComponent(JSON.stringify(message))}&sender_id=${userId}`;

            fetch(uploadUrl, {
                method: 'POST',
                body: formData
            })
            .then(response => {
                if (!response.ok) {
                    return response.text().then(text => {
                        throw new Error(`Ошибка сервера: ${response.status} ${text}`);
                    });
                }
                return response.json();
            })
            .then(data => {
                if (data.success) {
                    message.payload.chat_id = currentChatId;
                    ws.send(JSON.stringify(message));
                    
                    selectedFile = null;
                    fileInput.value = '';
                    uploadBtn.title = 'Прикрепить файл';
                } else {
                    addMessage('system', 'Ошибка загрузки файла: ' + (data.detail || 'Неизвестная ошибка'));
                }
            })
            .catch(error => {
                addMessage('system', 'Ошибка загрузки файла: ' + error.message);
            })
            .finally(() => {
                isSubmitting = false; // Снимаем блокировку после завершения
            });
        } else {
            message.payload.chat_id = currentChatId;
            lastSentMessage = {
                text: input.value,
                sender_id: userId,
                timestamp: Date.now()
            };
            ws.send(JSON.stringify(message));
            addMessage('client', input.value, new Date().toISOString(), userId);
            input.value = '';
            showButtons(false);
            isSubmitting = false;
        }
    } else {
        isSubmitting = false;
    }
});

satisfiedBtn.addEventListener('click', function() {
    if (!currentChatId) return;
    console.log("Satisfied button clicked for chat:", currentChatId);
    fetch(`/chat/${currentChatId}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'satisfied' })
    })
    .then(response => {
        if (!response.ok) {
            throw new Error(`Server error: ${response.statusText}`);
        }
        return response.json();
    })
    .then(data => {
        console.log('Feedback response:', data);
        // Сервер должен прислать 'status_update' через WS для обновления UI
        showButtons(false); // Скрываем кнопки сразу
    })
    .catch(error => {
        console.error('Error sending feedback:', error);
        addMessage('system', 'Не удалось отправить отзыв.');
    });
});

operatorBtn.addEventListener('click', function() {
    if (!currentChatId) return;
    console.log("Operator button clicked for chat:", currentChatId);
    fetch(`/chat/${currentChatId}/request_manager`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}) // Тело может быть пустым
    })
    .then(response => {
        if (!response.ok) {
            // Попробуем получить текст ошибки от сервера
            return response.text().then(text => {
                throw new Error(`Server error: ${response.status} ${response.statusText} - ${text}`);
            });
        }
        return response.json();
    })
    .then(data => {
        console.log('Request manager response:', data);
        // Сервер должен прислать 'status_update' через WS для обновления UI
        showButtons(false); // Скрываем кнопки сразу
    })
    .catch(error => {
        console.error('Error requesting manager:', error);
        addMessage('system', `Не удалось запросить оператора: ${error.message}`);
    });
});

newChatBtn.addEventListener('click', function() {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        addMessage('system', 'Нет соединения с сервером.');
        return;
    }
    console.log("New chat button clicked");
    const message = {
        type: 'start_new_chat',
        payload: {}
    };
    ws.send(JSON.stringify(message));
    messages.innerHTML = ''; // Очищаем старые сообщения на экране
    addMessage('system', 'Продолжаем чат. Введите ваше сообщение.');
    showNewChatButton(false); // Скрываем кнопку "Начать новый чат"
    form.style.display = 'flex'; // Показываем форму ввода
    input.disabled = false;
});

// Проверяем при загрузке страницы
document.addEventListener('DOMContentLoaded', function() {
    // Гарантируем, что кнопка скрыта при загрузке
    keyboardToggle.classList.remove('visible');
    keyboardToggle.classList.remove('active');
    isKeyboardVisible = false;
    lastWindowHeight = window.innerHeight;
});

// Слушаем фокус на поле ввода
input.addEventListener('focus', function() {
    // Показываем кнопку скрытия клавиатуры при фокусе
    keyboardToggle.classList.add('visible');
    keyboardToggle.classList.add('active');
    isKeyboardVisible = true;
    
    // Прокручиваем к полю ввода при фокусе
    setTimeout(() => {
        input.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 100);
});

// Слушаем потерю фокуса
input.addEventListener('blur', function() {
    clearTimeout(keyboardTimeout);
    keyboardTimeout = setTimeout(function() {
        keyboardToggle.classList.remove('visible');
        keyboardToggle.classList.remove('active');
        isKeyboardVisible = false;
    }, 100);
});

// Слушаем изменение размера окна (для определения появления/скрытия клавиатуры)
window.addEventListener('resize', function() {
    clearTimeout(keyboardTimeout);
    keyboardTimeout = setTimeout(function() {
        const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
        const currentWindowHeight = window.innerHeight;
        
        // Проверяем, изменилась ли высота окна (признак появления клавиатуры)
        const keyboardIsVisible = currentWindowHeight < lastWindowHeight;
        lastWindowHeight = currentWindowHeight;
        
        if (isMobile && keyboardIsVisible && input === document.activeElement) {
            keyboardToggle.classList.add('visible');
            keyboardToggle.classList.add('active');
            isKeyboardVisible = true;
        } else {
            keyboardToggle.classList.remove('visible');
            keyboardToggle.classList.remove('active');
            isKeyboardVisible = false;
        }
    }, 100);
});

// Функция для определения iOS устройства
function isIOS() {
    return /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
}

// Функция для определения расширения файла
function getFileExtension(media) {
    // Если file_id уже содержит расширение, возвращаем пустую строку
    if (media.file_id && media.file_id.includes('.')) {
        return '';
    }
    
    // Если есть оригинальное имя файла, берем расширение из него
    if (media.caption && media.caption.includes('.')) {
        return '.' + media.caption.split('.').pop().toLowerCase();
    }
    
    // Если нет, определяем по mime_type
    const mimeToExt = {
        'image/jpeg': '.jpg',
        'image/png': '.png',
        'image/gif': '.gif',
        'video/mp4': '.mp4',
        'video/quicktime': '.mov',
        'video/x-msvideo': '.avi',
        'application/pdf': '.pdf',
        'application/msword': '.doc',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
        'application/vnd.ms-excel': '.xls',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
        'text/plain': '.txt',
        'audio/mpeg': '.mp3',
        'audio/wav': '.wav',
        'audio/ogg': '.ogg'
    };
    
    return mimeToExt[media.mime_type] || '';
}

// Функции для работы с модальным окном
function openMediaModal(media) {
    const modal = document.getElementById('media-modal');
    const modalImage = document.getElementById('modal-image');
    const modalVideo = document.getElementById('modal-video');
    const modalAudio = document.getElementById('modal-audio');
    const modalDownload = document.getElementById('modal-download');
    
    // Скрываем все элементы
    modalImage.style.display = 'none';
    modalVideo.style.display = 'none';
    modalAudio.style.display = 'none';
    modalDownload.style.display = 'none';
    
    // Получаем расширение файла
    const fileExt = getFileExtension(media);
    
    // Формируем URL с расширением только если его нет в file_id
    const fileUrl = `/api/media/${media.file_id}${fileExt}`;
    modalDownload.href = fileUrl;
    
    // Для iOS открываем файл в новой вкладке, для других устройств - скачиваем
    if (isIOS()) {
        modalDownload.removeAttribute('download');
        modalDownload.target = '_blank';
        modalDownload.textContent = 'Открыть файл';
    } else {
        modalDownload.setAttribute('download', media.caption || 'file');
        modalDownload.removeAttribute('target');
        modalDownload.textContent = 'Скачать файл';
    }
    
    // Показываем соответствующий элемент в зависимости от типа медиа
    switch (media.type) {
        case 'photo':
            modalImage.src = fileUrl;
            modalImage.style.display = 'block';
            modalDownload.style.display = 'block';
            break;
        case 'video':
        case 'video_note':
            modalVideo.src = fileUrl;
            modalVideo.style.display = 'block';
            modalDownload.style.display = 'none';
            break;
        case 'voice':
        case 'audio':
            modalAudio.src = fileUrl;
            modalAudio.style.display = 'block';
            modalDownload.style.display = 'block';
            break;
        case 'document':
            modalDownload.style.display = 'block';
            break;
    }
    
    modal.style.display = 'block';
}

function closeMediaModal() {
    const modal = document.getElementById('media-modal');
    const modalVideo = document.getElementById('modal-video');
    const modalAudio = document.getElementById('modal-audio');
    
    modal.style.display = 'none';
    modalVideo.pause();
    modalAudio.pause();
}

// Добавляем обработчики для закрытия модального окна
document.addEventListener('DOMContentLoaded', function() {
    const modal = document.getElementById('media-modal');
    const closeBtn = document.querySelector('.media-modal-close');
    
    closeBtn.addEventListener('click', closeMediaModal);
    
    window.addEventListener('click', function(event) {
        if (event.target === modal) {
            closeMediaModal();
        }
    });
});

// Добавляем функцию для плавной прокрутки к последнему сообщению
function scrollToLastMessage(smooth = true) {
    const lastMessage = messages.lastElementChild;
    if (lastMessage) {
        lastMessage.scrollIntoView({ 
            behavior: smooth ? 'smooth' : 'auto', 
            block: 'end'
        });
    } else {
        messages.scrollTop = messages.scrollHeight;
    }
}

// --- Инициализация ---
// Дожидаемся готовности Telegram Web App API
tg.ready();
// Подключаемся к WebSocket при загрузке
connectWebSocket();