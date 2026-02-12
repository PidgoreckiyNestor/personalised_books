# Моделі бази даних та API схеми

**Файли:**
- `backend/app/models.py` — SQLAlchemy ORM моделі
- `backend/app/schemas.py` — Pydantic API схеми

---

## 1. Загальна архітектура даних

```
Frontend (React)          API (FastAPI)             Database (PostgreSQL)
     |                        |                           |
     |--- JSON request ------>|                           |
     |                        |--- Pydantic schema        |
     |                        |   (валідація вхідних)     |
     |                        |                           |
     |                        |--- SQLAlchemy ORM ------->|
     |                        |   (запис/читання)         |
     |                        |                           |
     |                        |<-- SQLAlchemy model ------|
     |                        |                           |
     |                        |--- Pydantic schema        |
     |                        |   (серіалізація вихідних) |
     |<--- JSON response -----|                           |
```

**Принцип:** Pydantic схеми визначають формат API (camelCase для фронтенду), ORM моделі — структуру БД (snake_case).

---

## 2. ORM моделі (SQLAlchemy)

### 2.1 Діаграма зв'язків між таблицями

```
User (users)
  |
  +--< Job (jobs)                    user_id FK
  |     |
  |     +--< JobArtifact             job_id FK
  |     |    (job_artifacts)
  |     |
  |     +--< CartItem                personalization_id FK → jobs.job_id
  |     |    (cart_items)
  |     |
  |     +--< OrderItem               personalization_id FK → jobs.job_id
  |          (order_items)
  |
  +--1 UserDeliveryAddress           user_id FK (PK = user_id)
  |    (user_delivery_addresses)
  |
  +--< PasswordResetToken            user_id FK
  |    (password_reset_tokens)
  |
  +--< Cart (carts)                  user_id FK
  |     |
  |     +--< CartItem                cart_id FK
  |          (cart_items)
  |
  +--< Order (orders)                user_id FK
        |
        +--< OrderItem               order_id FK
             (order_items)

Book (books)
  |
  +--< BookPreview                   slug FK
  |    (book_previews)
  |
  +--< CartItem                      slug FK
  |    (cart_items)
  |
  +--< Job                           slug (без FK, лише логічний зв'язок)
       (jobs)
```

---

### 2.2 Таблиця `jobs` — Персоналізації

Центральна таблиця системи. Кожен запис — одна персоналізація книги для однієї дитини.

| Поле | Тип | Призначення |
|------|-----|-------------|
| `job_id` | String PK | UUID ідентифікатор (генерується при створенні) |
| `user_id` | String FK? | Прив'язка до користувача (nullable — можна без реєстрації) |
| `slug` | String | Slug книги (напр. `wonderland-book`) |
| `status` | String | Стан job (state machine: `pending_analysis` → ... → `completed`) |
| `child_photo_uri` | String? | S3 URI завантаженого фото дитини |
| `child_name` | String | Ім'я дитини (для тексту на сторінках) |
| `child_age` | Integer | Вік дитини |
| `child_gender` | String? | Стать (визначається Qwen2-VL аналізом) |
| `caption_uri` | String? | Legacy: URI для підписів |
| `common_prompt` | String? | Текстовий prompt для Stable Diffusion (зовнішність дитини) |
| `analysis_json` | JSON? | Результат аналізу фото (Qwen2-VL) + retry дані |
| `result_uri` | String? | Legacy: URI результату |
| `avatar_url` | String? | URL аватара (кроп обличчя) |
| `preview_ready_at` | DateTime? | Коли preview став доступним |
| `cart_item_id` | String? | Зворотня прив'язка до елемента кошика |
| `created_at` | DateTime | Автоматичний timestamp |
| `updated_at` | DateTime | Автоматичний timestamp з оновленням |

**Важливо:** `analysis_json` — це JSONB поле, яке містить:
```json
{
  "age_group": "5-6",
  "gender": "girl",
  "hair_color": "dark brown",
  "hair_style": "long curly",
  "eye_color": "brown",
  "skin_tone": "olive",
  "prompt": "young girl, 5-6 years old, long dark brown curly hair...",
  "generation_retry": {
    "used": 1,
    "limit": 3,
    "randomize_seed": true
  }
}
```

---

### 2.3 Таблиця `job_artifacts` — Артефакти генерації

Зберігає згенеровані файли для кожного job. Винесена в окрему таблицю, щоб не змінювати основну таблицю `jobs` без міграцій.

| Поле | Тип | Призначення |
|------|-----|-------------|
| `id` | String PK | UUID |
| `job_id` | String FK | Прив'язка до Job |
| `stage` | String | `prepay` або `postpay` |
| `kind` | String | Тип артефакту: `page_png`, `spread_png`, `print_pdf`, `debug_json` |
| `page_num` | Integer? | Номер сторінки (якщо це сторінка) |
| `s3_uri` | String | S3 URI файлу |
| `meta` | JSON? | Додаткові метадані |
| `created_at` | DateTime | Timestamp |

---

### 2.4 Таблиця `users` — Користувачі

| Поле | Тип | Призначення |
|------|-----|-------------|
| `id` | String PK | UUID |
| `email` | String UNIQUE | Email (логін) |
| `password_hash` | String | bcrypt хеш паролю |
| `first_name` | String | Ім'я |
| `last_name` | String | Прізвище |
| `phone` | String? | Телефон |
| `created_at` / `updated_at` | DateTime | Timestamps |

---

### 2.5 Таблиця `user_delivery_addresses` — Адреси доставки

Один користувач — одна дефолтна адреса (PK = `user_id`).

| Поле | Тип | Призначення |
|------|-----|-------------|
| `user_id` | String PK/FK | Прив'язка до User (1:1) |
| `recipient` | String? | Отримувач |
| `city` | String? | Місто |
| `street` | String? | Вулиця |
| `house` | String? | Будинок |
| `apartment` | String? | Квартира |
| `postal_code` | String? | Поштовий індекс |
| `comment` | String? | Коментар до доставки |

---

### 2.6 Таблиця `password_reset_tokens`

| Поле | Тип | Призначення |
|------|-----|-------------|
| `token` | String PK | Токен скидання (генерується при запиті) |
| `user_id` | String FK | Користувач |
| `expires_at` | DateTime | Термін дії |
| `used` | Boolean | Чи використано |

---

### 2.7 Таблиця `books` — Каталог книг

| Поле | Тип | Призначення |
|------|-----|-------------|
| `slug` | String PK | Slug книги (`wonderland-book`) |
| `title` | String | Назва |
| `subtitle` | String? | Підзаголовок |
| `description` | Text | Опис книги (HTML або plain text) |
| `description_secondary` | Text? | Додатковий опис |
| `hero_image` | String | URL головного зображення |
| `gallery_images` | JSON? | Масив URL галереї |
| `bullets` | JSON? | Масив текстових bullet points |
| `age_range` | String | Вікова категорія: `"2-4"`, `"4-6"` |
| `category` | String | Категорія: `boy`, `girl`, `holiday`, `bestseller` |
| `price_amount` | Float | Ціна |
| `price_currency` | String | Валюта (`USD`) |
| `compare_at_price_amount` | Float? | Стара ціна (для знижки) |
| `discount_percent` | Float? | Відсоток знижки |
| `specs` | JSON? | Специфікації книги (idealFor, pages, shipping) |

---

### 2.8 Таблиця `book_previews` — Preview сторінки

Legacy таблиця для зберігання preview зображень книги. В новому pipeline preview будується динамічно з S3, але ця таблиця використовується як fallback.

| Поле | Тип | Призначення |
|------|-----|-------------|
| `id` | String PK | UUID |
| `slug` | String FK | Slug книги |
| `page_index` | Integer | Номер сторінки |
| `image_url` | String | URL зображення |
| `locked` | Boolean | Чи заблоковано (paywall) |
| `caption` | String? | Підпис сторінки |

---

### 2.9 Таблиці `carts` / `cart_items` — Кошик

```
Cart (carts)
  |
  +--< CartItem (cart_items)
         |
         +-- slug FK → books.slug
         +-- personalization_id FK → jobs.job_id
```

| CartItem поле | Тип | Призначення |
|---------------|-----|-------------|
| `id` | String PK | UUID |
| `cart_id` | String FK | Кошик |
| `slug` | String FK | Книга |
| `personalization_id` | String FK | Job персоналізації |
| `quantity` | Integer | Кількість (default 1) |
| `unit_price_amount` / `unit_price_currency` | Float / String | Ціна за одиницю |

---

### 2.10 Таблиці `orders` / `order_items` — Замовлення

**OrderStatus (enum):**
```
PENDING_PAYMENT → PROCESSING → DELIVERY → FULFILLED
                                        → CANCELLED
                                        → REFUNDED
```

| Order поле | Тип | Призначення |
|------------|-----|-------------|
| `id` | String PK | UUID |
| `number` | String UNIQUE | Читабельний номер замовлення |
| `user_id` | String FK | Покупець |
| `status` | Enum | Стан замовлення |
| `currency` | String | Валюта |
| `subtotal_amount` | Float | Сума товарів |
| `discount_amount` | Float | Знижка |
| `tax_amount` | Float | Податок |
| `shipping_amount` | Float | Доставка |
| `grand_total_amount` | Float | Загальна сума |
| `shipping_address` | JSON | Адреса доставки (snapshot) |
| `billing_address` | JSON? | Адреса для рахунку |
| `shipping_method` | JSON | Метод доставки (snapshot) |
| `payment_provider` | String? | `stripe`, `paypal`, `test` |
| `payment_token` | String? | Токен оплати |
| `placed_at` | DateTime | Дата оформлення |

**Чому адреса та shipping зберігаються як JSON?** — Це snapshot на момент замовлення. Якщо користувач змінить свою адресу, старі замовлення збережуть оригінальні дані.

---

## 3. Pydantic API Схеми

### 3.1 Маппінг ORM → API

| ORM модель | Pydantic схема | Де використовується |
|------------|----------------|---------------------|
| `Job` | `Personalization` | GET /status/, POST /upload_and_analyze/ |
| `Book` | `BookSummary`, `BookDetail` | GET /books/, GET /books/{slug} |
| `BookPreview` | `PreviewPage` | GET /preview/ |
| `Cart` + `CartItem` | `Cart`, `CartItem` | GET /cart/, POST /cart/items |
| `Order` + `OrderItem` | `Order`, `OrderItem` | GET /orders/, POST /orders/ |
| `User` | `UserProfile` | GET /me, POST /login |

---

### 3.2 Common (загальні) схеми

```python
Money(amount: float, currency: str)     # Ціна з валютою
ApiError(error: Dict)                    # Стандартна помилка
HealthResponse(status: str)              # Health check
```

---

### 3.3 Auth схеми

| Схема | Поля | Використовується в |
|-------|------|--------------------|
| `SignupRequest` | email, password (min 8), firstName, lastName | POST /auth/signup |
| `LoginRequest` | email, password | POST /auth/login |
| `AuthResponse` | token (JWT), user (UserProfile) | Відповідь login/signup |
| `UserProfile` | id, email, firstName, lastName, phone, delivery* | GET /me |
| `UserProfileUpdate` | всі поля optional | PATCH /me |
| `ForgotPasswordRequest` | email | POST /auth/forgot-password |
| `ResetPasswordRequest` | token, password (min 8) | POST /auth/reset-password |

**Примітка:** `UserProfile` включає поля доставки (deliveryCity, deliveryStreet, тощо), об'єднуючи дані з `users` + `user_delivery_addresses` таблиць.

---

### 3.4 Book (каталог) схеми

```
BookSummary (список)          BookDetail (деталі)
  slug                          extends BookSummary
  title                         description
  subtitle?                     descriptionSecondary?
  heroImage                     bullets[]
  ageRange                      galleryImages[]
  category                      specs (BookSpecs)
  price (Money)
  compareAtPrice? (Money)
  discountPercent?
  tags[] (BookTag)
```

**BookListResponse** — пагінований список:
```json
{
  "data": [BookSummary, ...],
  "meta": {
    "total": 12,
    "limit": 10,
    "nextCursor": "abc123"
  }
}
```

**BookHighlightsResponse** — секції для головної сторінки:
```json
{
  "sections": [
    {
      "key": "bestsellers",
      "title": "Бестселери",
      "ctaLabel": "Дивитись всі",
      "items": [BookSummary, ...]
    }
  ]
}
```

---

### 3.5 Personalization схеми

```python
class Personalization(BaseModel):
    id: str                            # job_id
    slug: str                          # Slug книги
    childName: str                     # Ім'я дитини
    childAge: int                      # Вік
    status: str                        # Стан job
    createdAt: datetime
    updatedAt: datetime
    previewReadyAt: Optional[datetime] # Коли preview готовий
    avatarUrl: Optional[str]           # Presigned URL на аватар
    preview: Optional[PreviewResponse] # Сторінки preview
    cartItemId: Optional[str]          # Елемент кошика
    generationRetry: Optional[GenerationRetry]

class PreviewResponse(BaseModel):
    pages: List[PreviewPage]           # Список сторінок
    unlockedCount: int                 # Скільки доступно без оплати
    totalCount: int                    # Загальна кількість

class PreviewPage(BaseModel):
    index: int                         # Номер сторінки
    imageUrl: str                      # Presigned URL
    locked: bool                       # Заблоковано (paywall)?
    caption: Optional[str]             # Підпис

class GenerationRetry(BaseModel):
    used: int                          # Скільки разів використано
    limit: int                         # Максимум (3)
    remaining: int                     # Залишилось
    allowed: bool                      # Чи можна ще
```

---

### 3.6 Cart (кошик) схеми

```python
class Cart(BaseModel):
    id: str
    currency: str
    items: List[CartItem]
    totals: CartTotals                 # subtotal, discount, tax, shipping, grand
    updatedAt: datetime

class CartItem(BaseModel):
    id: str
    slug: str                          # Книга
    title: str
    personalization: CartPersonalizationSummary  # {childName, childAge}
    quantity: int
    unitPrice: Money
    lineTotal: Money
    previewImage: Optional[str]        # URL першої сторінки preview
```

---

### 3.7 Order (замовлення) схеми

```python
class CreateOrderRequest(BaseModel):
    cartId: Optional[str]
    shippingAddress: Address            # Повна адреса
    billingAddress: Optional[Address]
    shippingMethodId: str               # ID методу доставки
    payment: PaymentInput               # provider + token
    email: Optional[EmailStr]

class PaymentInput(BaseModel):
    provider: str                       # "stripe" | "paypal" | "test"
    token: str                          # Токен від payment provider
    savePaymentMethod: bool = False

class Order(BaseModel):
    id: str
    number: str                         # Читабельний номер
    status: str
    placedAt: datetime
    currency: str
    totals: CartTotals
    items: List[OrderItem]
    shippingAddress: Address
    shippingMethod: ShippingMethod
    personalizationPreviews: List[PreviewPage]  # Preview для всіх книг
```

---

### 3.8 Shipping & Checkout

```python
class ShippingMethod(BaseModel):
    id: str                             # Напр. "standard", "express"
    label: str                          # "Стандартна доставка"
    description: Optional[str]
    amount: Money
    estimatedDaysMin: int
    estimatedDaysMax: int

class Address(BaseModel):
    firstName: str
    lastName: str
    company: Optional[str]
    line1: str                          # Адреса рядок 1
    line2: Optional[str]
    city: str
    region: Optional[str]
    postalCode: str
    countryCode: str                    # ISO 3166-1 alpha-2
    phone: Optional[str]
    email: Optional[EmailStr]
```

**CheckoutQuoteRequest/Response** — попередній розрахунок суми перед оплатою.

---

## 4. Конвенції іменування

| Контекст | Стиль | Приклад |
|----------|-------|---------|
| БД (SQLAlchemy) | snake_case | `child_name`, `user_id`, `created_at` |
| API (Pydantic) | camelCase | `childName`, `userId`, `createdAt` |
| URL paths | kebab-case | `/upload-and-analyze/`, `/forgot-password` |
| Таблиці БД | snake_case plural | `jobs`, `users`, `cart_items` |

---

## 5. Особливості архітектури

1. **Немає Alembic міграцій** — таблиці створюються через `Base.metadata.create_all()`. Нові таблиці (як `job_artifacts`, `user_delivery_addresses`) винесені окремо щоб не змінювати існуючі.

2. **String PK замість UUID/Integer** — всі Primary Key це String (UUID як текст). Простіше для передачі через API, але повільніше для індексів.

3. **JSON поля для гнучкості** — `analysis_json`, `specs`, `shipping_address`, `gallery_images` зберігаються як JSON. Дозволяє розширювати структуру без міграцій.

4. **Подвійне посилання Job ↔ CartItem** — `CartItem.personalization_id → Job.job_id` і `Job.cart_item_id → CartItem.id` (зворотнє). Зроблено для швидкого пошуку в обох напрямках.

5. **Snapshot адрес в Order** — `shipping_address` і `billing_address` зберігаються як JSON snapshot, а не як FK на `user_delivery_addresses`. Це гарантує що дані замовлення не зміняться якщо користувач оновить адресу.
