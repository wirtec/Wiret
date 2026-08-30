# Google News Full-Article Scraper → `news.json`

خبرها را از فید RSS گوگل‌نیوز می‌خواند، لینک ریدایرکت گوگل را به آدرس واقعی خبر تبدیل می‌کند،
وارد صفحهٔ ناشر می‌شود و **متن کامل خبر + عکس‌ها + متادیتا** را استخراج کرده و در `news.json` ذخیره می‌کند.
یک GitHub Action هم هر **۱ ساعت** به‌صورت خودکار اجرا می‌شود و نتیجه را کامیت می‌کند.

فید پیش‌فرض:

```
https://news.google.com/rss/search?q=technology&hl=en-US&gl=US&ceid=US:en
```

---

## ۱) چه چیزی استخراج می‌شود؟

از خود RSS:

| فیلد | توضیح |
|---|---|
| `title` | تیتر خبر (نام ناشر از انتهای تیتر جدا می‌شود) |
| `header` | هدر/منبع خبر (`<source>`) |
| `published_at` | زمان انتشار، نرمال‌شده به ISO-8601 UTC |
| `published_raw` | رشتهٔ اصلی `<pubDate>` |
| `description` / `description_html` | متن و HTML خام دیسکریپشن |
| `description_links` | لینک‌های داخل دیسکریپشن (همان لینکی که واردش می‌شویم) |

از صفحهٔ اصلی خبر (بعد از ورود به لینک):

| فیلد | توضیح |
|---|---|
| `url` / `final_url` | آدرس واقعی ناشر (بعد از رمزگشایی لینک گوگل) |
| `source` | نام سایت ناشر |
| `heading` | تگ `<h1>` صفحهٔ خبر |
| `authors` | نویسنده‌ها (meta + JSON-LD) |
| `article_published_at`, `article_modified_at` | زمان انتشار/ویرایش از خود صفحه |
| `summary` | خلاصه/lead (og:description) |
| **`content`** | **متن کامل خبر (پاراگراف‌ها، تیترهای فرعی، لیست‌ها)** |
| `content_html` | HTML تمیزشدهٔ بدنهٔ خبر |
| `word_count`, `char_count` | تعداد کلمه و کاراکتر |
| `lead_image` | عکس اصلی خبر |
| **`images[]`** | همهٔ عکس‌های خبر با `url`, `alt`, `caption`, `role` |
| `status` | `ok` \| `partial` \| `unresolved` \| `error` |
| `extractor` | موتور استخراج استفاده‌شده (`trafilatura`, `bs4`, `+amp`) |

---

## ۲) اجرای محلی

```bash
pip install -r requirements.txt

python main.py                          # فید پیش‌فرض technology
python main.py --max-items 10           # فقط ۱۰ خبر جدید
python main.py -q "artificial intelligence"   # جست‌وجوی دلخواه
python main.py --refresh                # خبرهای موجود را هم دوباره بخوان
python main.py -v                       # لاگ کامل دیباگ
```

گزینه‌های مهم CLI:

```
--feed URL          آدرس فید RSS
-q, --query TEXT    ساختن فید از عبارت جست‌وجو (به همراه --hl و --gl)
-o, --output FILE   فایل خروجی (پیش‌فرض news.json)
-n, --max-items N   حداکثر آیتم در هر اجرا (۰ = همه)
-w, --workers N     تعداد دانلود موازی (پیش‌فرض ۶)
--max-stored N      حداکثر خبر نگه‌داشته‌شده در فایل (پیش‌فرض ۵۰۰)
--max-images N      حداکثر عکس هر خبر (پیش‌فرض ۱۲)
--timeout / --retries / --delay
--refresh           بازخوانی خبرهای تکراری
```

همهٔ این‌ها با متغیر محیطی هم قابل تنظیم‌اند:
`FEED_URL`, `OUTPUT_FILE`, `MAX_ITEMS`, `WORKERS`, `TIMEOUT`, `RETRIES`,
`MAX_STORED`, `MAX_IMAGES`, `DELAY`, `REFRESH_EXISTING`, `USER_AGENT`.

---

## ۳) GitHub Action (هر ۱ ساعت)

### نصب ورک‌فلو (یک‌بار)

فایل‌های ورک‌فلو در `ci/workflows/` قرار دارند، چون توکن رباتی که این PR را ساخته
اجازهٔ نوشتن در `.github/workflows/` را ندارد (گیت‌هاب برای این مسیر مجوز `workflows`
لازم دارد). برای فعال‌سازی، یک‌بار این را اجرا کنید:

```bash
bash ci/install-workflows.sh   # کپی به .github/workflows/ + کامیت
git push
```

سپس در گیت‌هاب:
`Settings → Actions → General → Workflow permissions → Read and write permissions`
(تا جاب بتواند `news.json` را کامیت کند)، و برای تست فوری:
`Actions → Scrape Google News → Run workflow`.

### تنظیمات ورک‌فلو

فایل: `ci/workflows/scrape-news.yml` → `.github/workflows/scrape-news.yml`

```yaml
on:
  schedule:
    - cron: "0 * * * *"     # هر ساعت، دقیقهٔ ۰ (UTC)
  workflow_dispatch:         # اجرای دستی از تب Actions
```

در هر اجرا:
1. پایتون ۳٫۱۲ + نصب dependency‌ها (با کش pip)
2. `python main.py`
3. خلاصهٔ اجرا در **Job Summary** (جدول وضعیت خبرها)
4. اگر `news.json` تغییر کرده باشد → کامیت و پوش با `github-actions[bot]`
   (با `pull --rebase` و ۳ بار تلاش، برای جلوگیری از تصادم اجراهای هم‌زمان)
5. آپلود `news.json` به‌عنوان artifact

نکته: برای پوش شدن فایل، مخزن باید اجازهٔ نوشتن داشته باشد →
`Settings → Actions → General → Workflow permissions → Read and write permissions`.
(در ورک‌فلو هم `permissions: contents: write` تنظیم شده است.)

تنظیم فید در Action: مقدار `FEED_URL` را در بخش `env` ورک‌فلو عوض کنید.

---

## ۴) ساختار خروجی `news.json`

```json
{
  "feed_url": "https://news.google.com/rss/search?q=technology&hl=en-US&gl=US&ceid=US:en",
  "generated_at": "2026-08-29T23:57:41Z",
  "count": 8,
  "stats": { "feed_items": 8, "scraped_now": 3, "added": 2, "updated": 1, "ok": 7, "failed": 1, "duration_seconds": 41.2 },
  "articles": [
    {
      "id": "a1b2c3d4e5f6a7b8",
      "title": "Tech backlash reaches fever pitch...",
      "header": "CNBC",
      "published_at": "2026-08-29T12:00:01Z",
      "google_news_url": "https://news.google.com/rss/articles/CBMi...",
      "url": "https://www.cnbc.com/2026/08/29/tech-backlash-ai-data-centers.html",
      "description": "Tech backlash reaches fever pitch...  CNBC",
      "heading": "Tech backlash reaches fever pitch as AI angst collides with social media fears",
      "authors": ["..."],
      "summary": "...",
      "content": "Silicon Valley's technology leaders have long dreamed of...",
      "word_count": 1604,
      "lead_image": "https://image.cnbcfm.com/api/v1/image/108319525-....jpeg",
      "images": [{ "url": "...", "alt": "...", "caption": "...", "role": "lead" }],
      "status": "ok",
      "extractor": "trafilatura"
    }
  ]
}
```

---

## ۵) معماری

```
main.py                  ← CLI
scraper/
  config.py              ← تنظیمات (env + CLI)
  http_client.py         ← session به‌ازای هر ترد، retry با backoff نمایی
  rss.py                 ← پارس فید (title / header / time / description)
  resolver.py            ← رمزگشایی لینک گوگل‌نیوز → آدرس واقعی
  extractor.py           ← متن کامل + عکس‌ها + متادیتا (+ fallback نسخهٔ AMP)
  storage.py             ← merge/dedupe و نوشتن اتمیک news.json
  pipeline.py            ← ارکستراسیون موازی
tests/test_scraper.py    ← ۲۰ تست آفلاین (بدون نیاز به شبکه)
ci/
  workflows/             ← فایل‌های ورک‌فلو (با install-workflows.sh نصب می‌شوند)
  install-workflows.sh
```

**رمزگشایی لینک گوگل‌نیوز** (سه استراتژی، اولین موفق برنده):
1. RPC داخلی `batchexecute` (`Fbv4je`/`garturlreq`) با `data-n-a-id/ts/sg` صفحه — روش اصلی برای شناسه‌های رمزنگاری‌شدهٔ جدید
2. دیکد base64 شناسهٔ قدیمی
3. دنبال کردن ریدایرکت + خواندن `canonical` / `meta refresh`

**استخراج متن** (کیفیت‌محور):
1. `trafilatura` (بهترین کیفیت)
2. fallback با BeautifulSoup: انتخاب بهترین کانتینر بر اساس حجم پاراگراف‌ها + حذف منو/تبلیغ/نیوزلتر/کامنت
3. اگر سایت ربات را بلاک کرد (۴۰۳) یا متن کوتاه بود → تلاش خودکار روی نسخهٔ **AMP** (`link rel=amphtml` و الگوهای رایج)

**عکس‌ها**: `og:image`, `twitter:image`, JSON-LD، و `figure/picture/img` بدنهٔ خبر
(انتخاب بزرگ‌ترین گزینهٔ `srcset`، خواندن `figcaption`، حذف لوگو/آیکون/پیکسل ترکینگ،
و حذف نسخه‌های تکراری یک عکس در سایزهای مختلف).

**ذخیره‌سازی**: کلید یکتا = آدرس خبر؛ اجرای بعدی فقط خبرهای جدید را می‌خواند
(`skipped`)، فایل به‌صورت اتمیک (`tmp` + `os.replace`) نوشته می‌شود و خبرها
از جدید به قدیم مرتب می‌شوند.

---

## ۶) تست

```bash
python -m pytest tests -q     # 20 passed  (کاملاً آفلاین)
```

ورک‌فلو `tests.yml` (بعد از نصب) این تست‌ها را روی هر push و PR اجرا می‌کند.

---

## ۷) نکات و محدودیت‌ها

- برخی ناشرها (مثل Washington Post) هر دو نسخهٔ اصلی و AMP را برای ربات‌ها می‌بندند؛
  این خبرها با `status: "partial"` و `error: "download_failed"` ذخیره می‌شوند و
  متادیتای RSS آن‌ها (تیتر، هدر، زمان، دیسکریپشن، لینک) حفظ می‌شود.
- سایت‌های paywall معمولاً فقط پاراگراف‌های آزاد را می‌دهند (`status: "partial"`, `error: "short_text"`).
- `DELAY` و `WORKERS` را برای رعایت ادب نسبت به سرور ناشرها تنظیم کنید.
- محتوای خبرها متعلق به ناشران است؛ استفاده باید با شرایط استفادهٔ آن‌ها سازگار باشد.
