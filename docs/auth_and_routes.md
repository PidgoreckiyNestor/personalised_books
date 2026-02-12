# Авторизація та інші API роути

**Файли:**
- `backend/app/auth.py` — JWT утиліти та FastAPI dependencies
- `backend/app/routes/auth.py` — Auth ендпоінти (signup, login, etc.)
- `backend/app/routes/catalog.py` — Каталог книг
- `backend/app/routes/cart.py` — Кошик покупок
- `backend/app/routes/orders.py` — Замовлення та checkout
- `backend/app/routes/account.py` — Профіль користувача

---

## 1. Авторизація (`auth.py`)

### 1.1 Технологія

| Компонент | Технологія |
|-----------|-----------|
| Токен | JWT (JSON Web Token) |
| Алгоритм | HS256 (HMAC-SHA256) |
| Пароль | bcrypt hashing |
| Бібліотека JWT | PyJWT |
| Час дії токена | 7 днів (604800 хв) |

### 1.2 JWT Token

**Створення:**
```python
def create_access_token(user_id: str) -> str:
    payload = {
        "sub": user_id,     # Subject (UUID користувача)
        "exp": now + 7 days, # Expiration
        "iat": now           # Issued at
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")
```

**Декодування:**
```python
def decode_access_token(token: str) -> Optional[str]:
    payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    return payload.get("sub")  # user_id або None
```

### 1.3 FastAPI Dependencies (DI)

Три варіанти auth dependency для різних ендпоінтів:

| Dependency | Де використовується | Поведінка |
|-----------|---------------------|-----------|
| `get_current_user` | Cart, Orders, Account | **Обов'язкова** авторизація. 401 якщо немає токена |
| `get_current_user_optional` | Catalog previews | **Опціональна**. Повертає `User` або `None` |
| `get_current_user_header_or_query` | Download ендпоінти | Підтримує `Authorization: Bearer` **або** `?access_token=` в URL |

**Чому `get_current_user_header_or_query`?** — Для скачування файлів через `<a href="...">` або `window.open()` браузер не може передати `Authorization` header. Тому токен передається як query parameter.

### 1.4 Паролі

```python
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())
```

---

## 2. Auth Routes (`routes/auth.py`)

| Ендпоінт | Метод | Auth | Опис |
|---------|-------|------|------|
| `/auth/signup` | POST | - | Реєстрація |
| `/auth/login` | POST | - | Вхід |
| `/auth/logout` | POST | Yes | Вихід (client-side) |
| `/auth/forgot-password` | POST | - | Запит скидання паролю |
| `/auth/reset-password` | POST | - | Скидання паролю |

### `POST /auth/signup`
- Перевіряє що email не зайнятий (409 EMAIL_EXISTS)
- Хешує пароль через bcrypt
- Створює запис User
- Повертає JWT token + UserProfile

### `POST /auth/login`
- Знаходить User по email
- Перевіряє пароль через bcrypt (401 INVALID_CREDENTIALS)
- Повертає JWT token + UserProfile

### `POST /auth/forgot-password`
- Завжди повертає 202 (навіть якщо email не знайдено — security best practice)
- Створює PasswordResetToken (UUID, дійсний 1 годину)
- **TODO:** Відправка email з посиланням

### `POST /auth/reset-password`
- Перевіряє token (валідний, не використаний, не прострочений)
- Оновлює пароль, маркує token як використаний

---

## 3. Catalog Routes (`routes/catalog.py`)

| Ендпоінт | Метод | Auth | Опис |
|---------|-------|------|------|
| `/books` | GET | - | Список книг з пошуком і фільтрами |
| `/books/filters` | GET | - | Доступні фільтри (категорії, вікові групи) |
| `/books/highlights` | GET | - | Секції для головної сторінки |
| `/books/{slug}` | GET | - | Деталі книги |
| `/books/{slug}/related` | GET | - | Рекомендовані книги |
| `/books/{slug}/previews` | GET | Optional | Preview сторінки |

### `GET /books` — Пошук і фільтрація

**Параметри:**
| Параметр | Тип | Опис |
|---------|-----|------|
| `search` | str? | Пошуковий запит |
| `category` | str? | Фільтр категорії (boy, girl, holiday) |
| `ageRange` | str? | Фільтр віку (2-4, 4-6) |
| `limit` | int | Кількість (1-50, default 20) |
| `cursor` | str? | Cursor pagination |

**Fuzzy search:** Пошук з пропуском букв через PostgreSQL regex `~*`:
```
"прнцесса" → "п.*р.*н.*ц.*е.*с.*с.*а" → знайде "принцесса"
```

Кожне слово шукається у `title`, `subtitle`, `description`, `description_secondary`. Всі слова мають збігтися (AND логіка).

### `GET /books/highlights` — Секції для головної

```json
{
  "sections": [
    {"key": "new-arrivals", "title": "Новинки", "items": [...]},
    {"key": "bestsellers", "title": "Бестселлеры", "items": [...]},
    {"key": "boys", "title": "Для мальчиков", "items": [...]},
    {"key": "girls", "title": "Для девочек", "items": [...]}
  ]
}
```

### `GET /books/{slug}/previews` — Preview сторінки

**Два джерела даних:**
1. **Manifest-driven** (пріоритет) — читає manifest.json, показує перші 6 сторінок
2. **DB fallback** — читає з таблиці BookPreview

**Unlock логіка:**
- Default: перші 2 сторінки розблоковані
- Якщо є personalizationId + авторизований користувач + Job ready → всі розблоковані

**Mock-assets фільтр:** Функція `_is_mockish_preview_uri()` фільтрує демо/placeholder зображення:
- `illustrations/*` — legacy assets
- `twcstorage` — тестове сховище
- `responsive-images/` — маркетингові зображення
- `via.placeholder.com`, `picsum.photos` — placeholder сервіси

---

## 4. Cart Routes (`routes/cart.py`)

| Ендпоінт | Метод | Auth | Опис |
|---------|-------|------|------|
| `/cart` | GET | Yes | Отримати кошик |
| `/cart/items` | POST | Yes | Додати книгу в кошик |
| `/cart/items/{itemId}` | PATCH | Yes | Змінити кількість |
| `/cart/items/{itemId}` | DELETE | Yes | Видалити з кошика |
| `/checkout/shipping-methods` | GET | Yes | Методи доставки |
| `/checkout/quote` | POST | Yes | Розрахувати суму |

### `POST /cart/items` — Додавання в кошик

**Логіка:**
1. Знайти/створити кошик для користувача
2. Перевірити що Job існує і належить користувачу
3. Перевірити що Job має допустимий статус (preview_ready, prepay_ready, confirmed, completed)
4. Якщо елемент вже в кошику → збільшити кількість
5. Якщо новий → створити CartItem, змінити Job.status на "confirmed"

**Розрахунок:**
```
subtotal = Σ(unit_price × quantity)
tax = subtotal × 0.1 (10% demo)
grand_total = subtotal - discount + tax + shipping
```

### Методи доставки (hardcoded demo)

| ID | Назва | Ціна | Дні |
|----|-------|------|-----|
| `standard` | Standard Shipping | $5.99 | 5-7 |
| `express` | Express Shipping | $15.99 | 2-3 |
| `overnight` | Overnight Shipping | $29.99 | 1 |

---

## 5. Orders Routes (`routes/orders.py`)

| Ендпоінт | Метод | Auth | Опис |
|---------|-------|------|------|
| `/checkout/orders` | POST | Yes | Створити замовлення |
| `/orders` | GET | Yes | Список замовлень |
| `/orders/{orderId}` | GET | Yes | Деталі замовлення |
| `/orders/{orderId}/mark_paid` | POST | Yes | Позначити як оплачене |

### `POST /checkout/orders` — Створення замовлення

**Потік:**
```
1. Знайти кошик → перевірити що не пустий
2. Перевірити payment provider (stripe/paypal/test)
3. Знайти shipping method
4. Розрахувати totals
5. Створити Order + OrderItems
6. Видалити CartItems
7. Якщо payment=test → Order.status = PROCESSING
8. Тригернути postpay генерацію для кожного item:
   - stage_has_face_swap("postpay")?
     YES → build_stage_backgrounds_task (GPU)
     NO  → render_stage_pages_task (CPU)
```

**Номер замовлення:** `WW-{YYYYMMDD}-{6 random chars}` (напр. `WW-20260212-A3BX7K`)

### `POST /orders/{orderId}/mark_paid`

Ручна позначка оплати (для test provider). Змінює статус на PROCESSING і тригерить postpay генерацію.

### Virtual Order Status

Статус замовлення обчислюється динамічно через `compute_order_status()`:
- Якщо всі Job'и зі статусом "completed" → Order показується як "delivery"
- Інакше — використовується base status з БД

---

## 6. Account Routes (`routes/account.py`)

| Ендпоінт | Метод | Auth | Опис |
|---------|-------|------|------|
| `/account/profile` | GET | Yes | Отримати профіль |
| `/account/profile` | PUT | Yes | Оновити профіль |

### `PUT /account/profile`

Підтримує часткове оновлення через `__fields_set__`:
- Оновлює тільки передані поля
- Якщо є delivery-поля → створює/оновлює UserDeliveryAddress
- Порожні firstName/lastName ігноруються (не можна видалити ім'я)

---

## 7. Карта всіх API ендпоінтів

```
/auth/
  POST /auth/signup              (public)
  POST /auth/login               (public)
  POST /auth/logout              (auth)
  POST /auth/forgot-password     (public)
  POST /auth/reset-password      (public)

/books/
  GET  /books                    (public)
  GET  /books/filters            (public)
  GET  /books/highlights         (public)
  GET  /books/{slug}             (public)
  GET  /books/{slug}/related     (public)
  GET  /books/{slug}/previews    (optional auth)

/personalizations/ (див. personalizations_explained.md)
  POST /upload_and_analyze/      (optional auth)
  POST /generate/                (optional auth)
  GET  /status/{job_id}          (optional auth)
  GET  /preview/{job_id}         (optional auth)
  GET  /result/{job_id}          (optional auth)
  POST /avatar/{job_id}          (optional auth)
  POST /regenerate/{job_id}      (auth)
  POST /cancel/{job_id}          (optional auth)
  GET  /jobs                     (auth)
  GET  /preview/{id}/download/*  (auth - header or query)

/cart/
  GET  /cart                     (auth)
  POST /cart/items               (auth)
  PATCH /cart/items/{id}         (auth)
  DELETE /cart/items/{id}        (auth)

/checkout/
  GET  /checkout/shipping-methods (auth)
  POST /checkout/quote           (auth)
  POST /checkout/orders          (auth)

/orders/
  GET  /orders                   (auth)
  GET  /orders/{id}              (auth)
  POST /orders/{id}/mark_paid    (auth)

/account/
  GET  /account/profile          (auth)
  PUT  /account/profile          (auth)
```
