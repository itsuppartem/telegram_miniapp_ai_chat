import asyncio
import openai
import os
from dotenv import load_dotenv
from typing import Optional

from config import logger, AI_MODEL_API_KEY

CONTEXT_FILE = "company_context.txt"
AI_PROMPT_TEMPLATE = """You are a virtual assistant for the car rental service YOUR COMPANY in Belgrade. Your task is to answer customer questions using **exclusively** the **CONTEXT** provided below.

**MAIN INSTRUCTIONS:**

1.  **Strict Context Adherence:** Use *only* information contained in the **CONTEXT**. Do not add external information or use your general knowledge about car rentals.
2.  **No Hallucinations:** If the **CONTEXT** doesn't contain an exact answer to the user's question, you **must not** make up, guess, or assume an answer.
3.  **"Contact Manager" Protocol:** If you cannot find an answer in the **CONTEXT**, **always** respond with the following phrase (or a very similar variant):
    "Unfortunately, I cannot find exact information regarding your question. For current information or to answer a specific request, please call the VROOM operator using the button below"
4.  **Recognizing Unanswerable Questions:** Be attentive to questions that the **CONTEXT** likely **does not contain answers for**. These include:
    *   Checking availability of **specific cars** for **specific dates** in real-time (the text mentions a website/bot for this, but you cannot verify this yourself).
    *   Calculating **exact final cost** of rental for complex requests (e.g., half-day rental, non-standard dates/return locations, if not explicitly described).
    *   Questions about **technical issues** with the booking website or chat-bot (e.g., "can't send address", "bot can't find address").
    *   Detailed questions about **YOUR COMPANY Service (car repair)** beyond brief mentions in the text.
    *   Checking **status of specific booking** ("waiting for contact about order", "manager hasn't contacted").
    *   Information about user's **personal bonus points balance**.
    *   Confirming possibility of **specific actions** not described in text (e.g., pre-signing contract for visa, ski rack availability, exact engine power of Ibiza/Fabia).
    *   Any questions requiring **personal consultation, clarification of specific order details**, or information completely absent from text.
    *   Questions about **gift certificates** or **coupons** if no details in context.
    *   Requests for **on-site car inspection before purchase**.
    *   Questions about **electric vehicles** (if not mentioned in context).
    *   Requests for **cargo vans** (if not mentioned in context).
    In such cases, **always** use the response from point 3.
5.  **Accuracy and Language:** Answer precisely, to the point, and in Russian. If information exists, try to give a direct answer, referencing relevant parts of the context when appropriate.

**CONTEXT:**
{context}

**CUSTOMER QUESTION:**
{question}

**YOUR ANSWER:**"""
_context_cache = None


def load_context() -> str:
    global _context_cache
    if _context_cache:
        return _context_cache
    try:
        with open(CONTEXT_FILE, "r", encoding="utf-8") as f:
            _context_cache = f.read()
        logger.info(f"Контекст из {CONTEXT_FILE} успешно загружен.")
        return _context_cache
    except FileNotFoundError:
        logger.error(f"Файл контекста {CONTEXT_FILE} не найден!")
        return "Контекст не загружен."
    except Exception as e:
        logger.error(f"Ошибка чтения файла контекста {CONTEXT_FILE}: {e}")
        return "Ошибка загрузки контекста."


async def get_ai_response(user_message: str) -> Optional[str]:
    """
    Получает ответ от нейросети через OpenAI API.
    """
    context = load_context()
    prompt = AI_PROMPT_TEMPLATE.format(context=context, question=user_message)
    logger.info(f"Запрос к AI с промптом: {prompt[:200]}...")

    try:
        load_dotenv(override=True)

        base_url = os.getenv("OPENAI_API_BASE")
        api_key = os.getenv("OPENAI_API_KEY")
        model_name = os.getenv("OPENAI_MODEL_NAME")

        if not base_url:
            logger.error("OPENAI_API_BASE не найден в переменных окружения!")
            return None

        logger.info(f"Подключение к AI с параметрами:")
        logger.info(f"Base URL: {base_url}")
        logger.info(f"API Key: {api_key}")
        logger.info(f"Model: {model_name}")
        logger.info(f"Prompt: {prompt}")
        client = openai.OpenAI(base_url=base_url, api_key=api_key)

        response = await asyncio.to_thread(client.chat.completions.create, model=model_name,
            messages=[{"role": "system", "content": "Ты - полезный ассистент VROOM."},
                {"role": "user", "content": prompt}], temperature=1, max_tokens=1000)

        ai_result = response.choices[0].message.content.strip()
        logger.info("AI сгенерировал ответ.")
        return ai_result

    except Exception as e:
        logger.error(f"Ошибка при запросе к AI API: {e}")
        return None
