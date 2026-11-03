<a id="turkce"></a>

# BIST Şirketleri İçin Finansal Veri Pipeline'ı

**Türkçe** | [English](#english)

## Kısaca

BIST şirketlerinin KAP'ta yayımlanan finansal tablolarını indirip düzenli, dönemler arasında karşılaştırılabilir bir veri setine dönüştüren bir projedir. Amaç, farklı raporlardaki dağınık bilgileri tek tek aramak yerine şirketlerin finansal durumunu ve zaman içindeki değişimini inceleyebileceğiniz bir temel sunmaktır.

Proje; gelir tablolarını, bilançoları, nakit akış tablolarını ve özkaynak değişim tablolarını işler. Enflasyon etkisini dikkate alan değerler, USD karşılıkları, son 12 aylık göstergeler ve finansal oranlar da üretir. Sonuçları PostgreSQL, Jupyter Notebook veya destekleyen bir raporlama aracıyla inceleyebilirsiniz.

**Bu proje hisse fiyatı tahmini yapmaz ve yatırım tavsiyesi vermez.** Önemli analizleri mutlaka KAP'taki orijinal raporlarla karşılaştırın.

Finans öğrencileri, araştırmacılar ve şirket finansallarını kendi yöntemleriyle incelemek isteyen herkes için hazırlanmıştır.

### Projenin devamında yapmayı planladıklarım:
- Veri altyapısını şirket faaliyet raporları ve makroekonomik göstergelerle zenginleştirmek.
- Eğilimleri incelemek, şirketleri karşılaştırmak ve zaman içindeki değişimleri takip etmek için raporlama ve görselleştirme araçları geliştirmek.
- Bildirimleri özetleyen; dikkat çekebilecek riskleri, fırsatları ve örüntüleri ortaya çıkaran yapay zeka destekli araçlar oluşturmak.
- Bu farklı veri ve analizleri bir araya getirerek potansiyel yatırım fırsatlarını araştırmak; sonuçları kesin tahmin veya yatırım tavsiyesi değil, karar desteği olarak değerlendirmek.

## Neler yapabilirsiniz?

- Bir şirketin gelir, varlık, borç ve nakit akışı gibi kalemlerini çeyreklik olarak inceleyebilirsiniz.
- Enflasyon etkisi azaltılmış değerlerle farklı yılları daha anlamlı biçimde karşılaştırabilirsiniz.
- Günlük hisse fiyatlarını indirip finansal tablo verileriyle birlikte inceleyebilirsiniz. (Fiyatlar İş Yatırım web sitesinden gelir)
- USD karşılıklarını, son 12 aylık toplamları ve hesaplanan finansal oranları sorgulayabilirsiniz.
- Hazırlanan verileri kendi şirket karşılaştırmalarınızda, grafiklerinizde ve araştırmalarınızda kullanabilirsiniz.
- PostgreSQL veritabanını Jupyter Notebook'a veya uyumlu bir iş zekası aracına (Power BI, Tableau gibi) bağlayabilirsiniz.

## Üretilen çıktılar

Ana çıktı, `bist_finance` PostgreSQL veritabanındaki Bronze/Silver/Gold tablolarıdır. Başlıca veri kümeleri:

| Tablo | İçerik |
| --- | --- |
| `bronze.statement_filings` ve `bronze.statement_rows` | KAP dosyalarından alınan kaynak kayıtlar |
| `silver.financial_observations` | Seçilmiş ve temizlenmiş finansal tablo satırları |
| `silver.monthly_macro_data` | Aylık enflasyon ve USD/TRY referans verileri |
| `silver.daily_prices` | Günlük hisse fiyatları |
| `gold.financial_values` ve `gold.financial_ratios` | Çeyreklik, TTM, enflasyona göre düzeltilmiş değerler ve oranlar |
| `gold.price_series` | Enflasyona göre düzeltilmiş günlük kapanışlar |

Finansal tablolardaki kalemler; şirket, yıl, çeyrek, kalem adı ve mümkün olduğunda rapordaki üst başlıklarıyla birlikte saklanır. Böylece aynı ada sahip kalemlerin farklı bölümlerde yer alması daha kolay ayırt edilir. Ayrıca nominal tutarlar, enflasyona göre düzeltilmiş tutarlar ve USD karşılıkları ayrı alanlarda bulunur.

## Hızlı başlangıç

Bilgisayarınızda Python 3.11 veya daha yenisi kurulu olmalıdır. Önce işletim sisteminize uygun ortamı hazırlayın.

### Windows

PowerShell'i proje klasöründe açın ve çalıştırın:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Şirket bilgileri `bist_finance` adlı PostgreSQL veritabanındaki `metadata` şemasında saklanır. Bağlantıyı `BIST_FINANCE_DSN` ortam değişkeniyle tanımlayın; örneğin `dbname=bist_finance host=localhost user=YOUR_USER`. PostgreSQL metadata şemasını hazırladıktan sonra eski JSON kayıtlarını bir kez içe aktarın:

```bash
python -m services.import_company_metadata
```

Bu komut JSON dosyalarını silmez; yalnızca mevcut kayıtları PostgreSQL'e aktarır. Sonraki şirket güncellemeleri, finansal veriler ve sembol aramaları PostgreSQL'i kullanır.

PowerShell sanal ortamı etkinleştirmeyi engellerse, `.\.venv\Scripts\python.exe` yolunu `python` yerine kullanabilirsiniz.

### macOS

Terminal'i proje klasöründe açın ve çalıştırın:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Ortamı hazırladıktan sonra, üç şirketin 2017 Q1 ile 2026 Q2 arasındaki verilerini indirmek ve işlemek için:

```bash
python pipeline.py run --symbols THYAO TUPRS BIMAS --begin-year 2017 --begin-quarter 1 --end-year 2026 --end-quarter 2
```

Bu komut PostgreSQL şemasını hazırlar, referans verilerini yeniler, KAP arşivlerini indirir ve seçilen dönemleri işler. Enflasyon hedefi, veritabanındaki en güncel ay olarak otomatik seçilir.

## İşlemleri ayrı ayrı çalıştırma

Her aşamayı ayrı çalıştırmak da mümkündür:

```bash
# Yalnızca KAP arşivlerini indir
python pipeline.py download --symbols THYAO TUPRS BIMAS --begin-year 2017 --end-year 2026

# Enflasyon ve USD/TRY referans tablolarını yenile
python pipeline.py update-reference-data

# Daha önce indirilmiş arşivleri okuyup veritabanını güncelle
python pipeline.py update-database --symbols THYAO TUPRS BIMAS --begin-year 2017 --begin-quarter 1 --end-year 2026 --end-quarter 2

# Günlük hisse fiyatlarını indir
python pipeline.py update-prices --symbols THYAO TUPRS BIMAS --begin-date 2017-01-01 --end-date 2026-06-30

# Fiyatları indirmeden enflasyon düzeltmesini yeniden hesapla
python pipeline.py adjust-prices --price-level-year 2026 --price-level-month 6
```

`run` komutu PostgreSQL şemasını hazırlar, referans verilerini yeniler ve KAP arşivlerini indirir. Bu adımlar zaten tamamsa `--skip-download` veya `--skip-reference-data` seçeneklerini kullanabilirsiniz. Şirket simgelerini yazmazsanız PostgreSQL metadata tablosundaki tüm şirketler işleme alınır.

Belirli bir enflasyon hedefi kullanmak isterseniz hem `--price-level-year` hem de `--price-level-month` seçeneklerini birlikte verin. Örneğin `--price-level-year 2026 --price-level-month 9`.

## Veri güvenliği ve kapsam

- Yeni finansal veriler tamamlanıp yayımlanmaya hazır olmadan mevcut PostgreSQL kayıtları silinmez; işlem başarısız olursa önceki etkin veriler korunur.
- İstenen bir şirket ve dönem için lokal rapor dosyası yoksa o dönemdeki mevcut PostgreSQL kayıtları korunur ve eksikler loglarda bildirilir. Hiç uygun dosya bulunamazsa veritabanı değiştirilmeden işlem durur.
- Başlangıç dönemi bitiş döneminden sonraysa komut herhangi bir işe başlamadan hata verir.
- KAP arşivleri otomatik silinmez; disk alanını yönetmek için bunları ayrıca temizleyebilirsiniz.

## Veri kaynakları ve dikkat edilmesi gerekenler

- Finansal tablolar KAP'ta yayımlanan şirket raporlarından alınır.
- Enflasyon verileri TCMB'nin tüketici fiyatları verilerinden alınır.
- USD/TRY referans verileri Frankfurter servisinden alınır ve aylık değerlere dönüştürülür.
- Günlük hisse fiyatı verileri İş Yatırım'dan alınır. Tekrar çalıştırma yalnızca seçilen şirketleri ve tarih aralığını yeniler.
- Bazı finansal tablolar KAP'ta çeyrek içi değerler yerine tüm yıl için kümülatif değerler ile sunulur. Proje, uygun gelir tablosu değerlerini tek çeyrek değerine dönüştürür.
- Kaynak raporların biçimi değişebilir; otomatik okuma her kalemi kusursuz tanımayabilir. Veritabanı analiz içindir, resmi raporun yerine geçmez.

## PostgreSQL finansal veri akışı

Şirket metaverisi PostgreSQL'de tutuluyor. Finansal veriler için eklenen medallion akışı KAP kaynaklarını `bronze` altında korur, seçilen ve temizlenen kayıtları `silver` altında yayımlar, çeyreklik değerleri, enflasyon düzeltmelerini, TTM toplamlarını ve oranları `gold` altında üretir. Her çalıştırma ve kalite kontrol sonucu `ops` şemasında izlenir. Yeni hesaplamalar ayrı sürüm olarak saklanır; `gold.current_financial_values`, `gold.current_financial_ratios` ve `gold.current_price_series` görünümleri en son yayımlanan sürümü gösterir.

Tüm finansal ve fiyat verileri PostgreSQL'e yazılır. Ana CLI komutları PostgreSQL kullanır:

```bash
python pipeline.py migrate-postgres
python pipeline.py update-reference-data
python pipeline.py update-database --symbols THYAO TUPRS BIMAS --begin-year 2017 --begin-quarter 1 --end-year 2026 --end-quarter 2
python pipeline.py update-prices --symbols THYAO TUPRS BIMAS --begin-date 2017-01-01 --end-date 2026-06-30
python pipeline.py adjust-prices --price-level-year 2026 --price-level-month 6
```

İndirme ve finansal tabloları PostgreSQL'e işleme adımlarını tek seferde çalıştırmak için `run` komutunu kullanın. Hisse fiyatları ayrı bir komutla alınır. Önce `BIST_FINANCE_DSN` değişkeninin `bist_finance` veritabanına bağlandığını doğrulayın. Tabloları Jupyter'dan aynı PostgreSQL bağlantısıyla sorgulayabilirsiniz.

## Proje yapısı

```text
database/migrations/    PostgreSQL metadata ve medallion şemaları
models/                 Şirket ve finansal tablo veri yapıları
read_statements/        Rapor arşivlerini bulma, okuma ve seçme
services/               PostgreSQL, indirme ve referans verisi servisleri
tests/                  Otomatik kontroller
pipeline.py             Komut satırı giriş noktası
```

## Geliştirici notu

Testleri çalıştırmak için proje klasöründe `python -m pytest` komutunu kullanın. Kod biçimi ve yaygın hatalar için `ruff check .` kullanılabilir.

---

<a id="english"></a>

# BIST Financial Data Pipeline

[Türkçe](#turkce) | **English**

## At a glance

This project turns financial statements published by BIST companies on KAP into an organized dataset that is easier to compare across reporting periods. Instead of searching through separate reports one by one, you get a consistent starting point for exploring a company’s financial position and how it changes over time.

The project processes income statements, balance sheets, cash flow statements, and statements of changes in equity. It also calculates inflation-adjusted values, USD equivalents, totals for the most recent four quarters, and financial ratios. You can explore the results in PostgreSQL, a Jupyter notebook, or a compatible reporting tool.

**This project does not predict stock prices or provide investment advice.** Automated processing can make mistakes, so check important findings against the original KAP reports.

It is intended for finance students, researchers, and anyone who wants to explore company financials using their own methods.

### What I plan to do in the continuation of this project
- Expand the dataset with company activity reports and macroeconomic indicators.
- Build reporting and visualization tools to explore trends, compare companies, and monitor changes over time.
- Develop AI-assisted tools to summarize disclosures and surface potentially relevant risks, opportunities, and patterns.
- Combine these perspectives to research potential investment opportunities, while treating the results as decision support, not guaranteed predictions or investment advice.

## What you can do with it

- Explore items such as revenue, assets, debt, and cash flow over time.
- Compare different years using values adjusted for inflation.
- Download daily historical prices and explore them alongside the processed financial statements.
- Query USD equivalents, trailing-twelve-month totals, and calculated financial ratios.
- Use the prepared data for company comparisons, charts, and your own research.
- Connect PostgreSQL to a Jupyter notebook or compatible business-intelligence tool.

## Outputs

The main output is the Bronze/Silver/Gold data in the `bist_finance` PostgreSQL database. Key datasets include:

| Table | Contents |
| --- | --- |
| `bronze.statement_filings` and `bronze.statement_rows` | Raw KAP filing records |
| `silver.financial_observations` | Selected, cleaned statement observations |
| `silver.monthly_macro_data` | Monthly CPI and USD/TRY reference data |
| `silver.daily_prices` | Daily historical prices |
| `gold.financial_values` and `gold.financial_ratios` | Quarterly values, TTM totals, inflation adjustments, and ratios |
| `gold.price_series` | Inflation-adjusted daily closing prices |

Statement rows include the company, year, quarter, item name, and, when recognized, the item’s parent headings. This helps distinguish same-named items that appear in different sections. Nominal amounts, inflation-adjusted amounts, and USD equivalents are stored separately.

## Quick start

You need Python 3.11 or newer. First set up the environment for your operating system.

### Windows

Open PowerShell in the project folder and run:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell prevents activation, use `.\.venv\Scripts\python.exe` in place of `python`.

### macOS

Open Terminal in the project folder and run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Company metadata is stored in the `metadata` schema of the PostgreSQL database `bist_finance`. Set `BIST_FINANCE_DSN` to your PostgreSQL connection details (for example, `dbname=bist_finance host=localhost user=YOUR_USER`), then import your existing JSON records once:

```bash
python -m services.import_company_metadata
```

This imports the records without deleting the JSON files. The pipeline uses PostgreSQL for company metadata and all financial, price, and reference data.

Once the environment is ready, download and process THYAO, TUPRS, and BIMAS from 2017 Q1 through 2026 Q2:

```bash
python pipeline.py run --symbols THYAO TUPRS BIMAS --begin-year 2017 --begin-quarter 1 --end-year 2026 --end-quarter 2
```

This prepares the PostgreSQL schema, refreshes reference data, downloads KAP archives, and processes the selected periods. The inflation target defaults to the latest month stored in PostgreSQL.

## Run individual steps

You can also run each step separately:

```bash
# Download KAP archives only
python pipeline.py download --symbols THYAO TUPRS BIMAS --begin-year 2017 --end-year 2026

# Refresh inflation and USD/TRY reference tables
python pipeline.py update-reference-data

# Process archives that are already stored locally
python pipeline.py update-database --symbols THYAO TUPRS BIMAS --begin-year 2017 --begin-quarter 1 --end-year 2026 --end-quarter 2

# Download daily prices
python pipeline.py update-prices --symbols THYAO TUPRS BIMAS --begin-date 2017-01-01 --end-date 2026-06-30

# Recalculate stored prices to a selected inflation level without downloading
python pipeline.py adjust-prices --price-level-year 2026 --price-level-month 6
```

The `run` command downloads archives and refreshes reference data by default. Use `--skip-download` or `--skip-reference-data` when those steps are already complete. If you omit `--symbols`, the pipeline processes every company in the configured company list.

To use a specific inflation target, provide both `--price-level-year` and `--price-level-month`, for example `--price-level-year 2026 --price-level-month 9`.

## Data protection and behavior

- New financial data is committed to PostgreSQL only after parsing and staging succeeds. An interrupted run preserves the previously active data.
- If a local report is missing for a requested company-period, the existing PostgreSQL data for that period is preserved and the gap is logged.
- Reversed start and end periods are rejected before work begins.

## Data sources and limitations

- Financial statements come from company reports published on KAP.
- Inflation data comes from the Central Bank of the Republic of Türkiye (TCMB) consumer price data.
- USD/TRY reference data comes from the Frankfurter service and is summarized into monthly values.
- Daily price data comes from Is Yatirim. Each company is saved separately, so completed companies remain in the database if a later company fails. Re-running replaces only the selected symbols and date range.
- Some KAP files show year-to-date cumulative figures rather than standalone quarterly values. The project converts applicable income statement values into standalone quarters.
- KAP report layouts can change, and automatic parsing may not recognize every item perfectly. The database supports analysis; it does not replace the official reports.
- Inflation and exchange-rate adjustments are limited by the source frequency and monthly reference method. They do not represent a company’s actual foreign-currency transactions or the inflation applicable on each item’s transaction date.

## PostgreSQL financial pipeline

Company metadata already lives in PostgreSQL. The new medallion path keeps KAP source records in `bronze`, publishes selected and cleaned observations in `silver`, and builds quarterly values, inflation adjustments, TTM totals, and ratios in `gold`. Run history and data-quality checks are tracked in `ops`. Each calculation is stored as a separate generation; `gold.current_financial_values`, `gold.current_financial_ratios`, and `gold.current_price_series` expose the latest published generation.

All financial and price data is stored in PostgreSQL. The primary commands are:

```bash
python pipeline.py migrate-postgres
python pipeline.py update-reference-data
python pipeline.py update-database --symbols THYAO TUPRS BIMAS --begin-year 2017 --begin-quarter 1 --end-year 2026 --end-quarter 2
python pipeline.py update-prices --symbols THYAO TUPRS BIMAS --begin-date 2017-01-01 --end-date 2026-06-30
python pipeline.py adjust-prices --price-level-year 2026 --price-level-month 6
```

Use `run` to download and process financial statements in one run. Historical prices are fetched separately. Confirm that `BIST_FINANCE_DSN` points to the `bist_finance` database before running these commands. Query the tables from Jupyter using the same PostgreSQL connection settings.

## Project layout

```text
database/migrations/    PostgreSQL metadata and medallion schemas
models/                 Company and financial-statement data structures
read_statements/        Finding, reading, and selecting report archives
services/               PostgreSQL, download, and reference-data services
tests/                  Automated checks
pipeline.py             Command-line entry point
```

## For contributors

Run the tests from the project folder with `python -m pytest`. Run `ruff check .` for code-quality checks.
