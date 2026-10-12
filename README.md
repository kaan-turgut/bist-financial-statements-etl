<a id="turkce"></a>

# BIST Şirketleri İçin Finansal Veri Pipeline'ı

**Türkçe** | [English](#english)

## Kısaca

BIST şirketlerinin KAP'ta yayımlanan finansal tablolarını indirip düzenli, dönemler arasında karşılaştırılabilir bir veri setine dönüştüren bir projedir. Amaç, farklı raporlardaki dağınık bilgileri tek tek aramak yerine şirketlerin finansal durumunu ve zaman içindeki değişimini inceleyebileceğiniz bir temel sunmaktır.

Proje; gelir tablolarını, bilançoları, nakit akış tablolarını ve özkaynak değişim tablolarını işler. Enflasyon etkisini dikkate alan değerler, USD karşılıkları, son 12 aylık göstergeler ve finansal oranlar da üretir. Sonuçları Jupyter Notebook, SQLite veya destekleyen bir raporlama aracıyla inceleyebilirsiniz.

**Bu proje hisse fiyatı tahmini yapmaz ve yatırım tavsiyesi vermez.** Veriler otomatik olarak işlense de önemli analizleri KAP'taki asıl raporlarla karşılaştırın.

Finans öğrencileri, araştırmacılar ve şirket finansallarını kendi yöntemleriyle incelemek isteyen herkes için hazırlanmıştır.

### Projenin devamında yapmayı planladıklarım:
- Veri altyapısını şirket faaliyet raporları ve makroekonomik göstergelerle zenginleştirmek.
- Eğilimleri incelemek, şirketleri karşılaştırmak ve zaman içindeki değişimleri takip etmek için raporlama ve görselleştirme araçları geliştirmek.
- Bildirimleri özetleyen; dikkat çekebilecek riskleri, fırsatları ve örüntüleri ortaya çıkaran yapay zekâ destekli araçlar oluşturmak.
- Bu farklı veri ve analizleri bir araya getirerek potansiyel yatırım fırsatlarını araştırmak; sonuçları kesin tahmin veya yatırım tavsiyesi değil, karar desteği olarak değerlendirmek.

## Neler yapabilirsiniz?

- Bir şirketin gelir, varlık, borç ve nakit akışı gibi kalemlerini çeyreklik olarak inceleyebilirsiniz.
- Enflasyon etkisi azaltılmış değerlerle farklı yılları daha anlamlı biçimde karşılaştırabilirsiniz.
- Günlük hisse fiyatlarını indirip finansal tablo verileriyle birlikte inceleyebilirsiniz. (Fiyatlar İş Yatırım web sitesinden gelir)
- USD karşılıklarını, son 12 aylık toplamları ve hesaplanan finansal oranları sorgulayabilirsiniz.
- Hazırlanan verileri kendi şirket karşılaştırmalarınızda, grafiklerinizde ve araştırmalarınızda kullanabilirsiniz.
- SQLite veritabanını Jupyter Notebook'a veya uyumlu bir iş zekâsı aracına (Power BI, Tableau gibi) bağlayabilirsiniz.

## Üretilen çıktılar

Ana çıktı, proje klasöründeki `bist_data.db` dosyasıdır. Bu, tabloları sorgulanabilen yerel bir veritabanıdır. Başlıca tablolar:

| Tablo | İçerik |
| --- | --- |
| `income_statement` | Gelir tablosu kalemleri ve türetilen dönem göstergeleri |
| `balance_sheet` | Bilanço kalemleri |
| `cash_flow` | Nakit akış kalemleri |
| `equity_change` | Özkaynak değişim kalemleri |
| `ratios` | Hesaplanan finansal oranlar |
| `inflation_table` | Aylık tüketici enflasyonu verileri |
| `usd_try_table` | USD/TRY aylık referans kurları |
| `prices` | Günlük hisse fiyatları, işlem hacmi, piyasa değeri ve enflasyon düzeltmeli kapanış |

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

Bu komut referans verilerini yeniler, KAP arşivlerini indirir ve seçilen dönemleri veritabanına işler. Enflasyon hedefi, veritabanındaki en güncel ay olarak otomatik seçilir. Daha sonra `bist_data.db` dosyasını Jupyter Notebook veya bir SQLite görüntüleyiciyle açabilirsiniz.

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

`run` komutu indirme ve referans verisi yenileme adımlarını varsayılan olarak yapar. Bu adımlar zaten tamamsa `--skip-download` veya `--skip-reference-data` seçeneklerini kullanabilirsiniz. Şirket simgelerini yazmazsanız listedeki tüm şirketler işleme alınır.

Belirli bir enflasyon hedefi kullanmak isterseniz hem `--price-level-year` hem de `--price-level-month` seçeneklerini birlikte verin. Örneğin `--price-level-year 2026 --price-level-month 9`.

## Veri güvenliği ve kapsam

- Veritabanı güncellenmeden önce mevcut veritabanının doğrulanmış bir kopyası `database_backups/` klasörüne kaydedilir. Dosya adında yedekleme zamanı bulunur.
- İstenen bir şirket ve dönem için yerel rapor dosyası yoksa o dönemdeki mevcut veritabanı satırları korunur ve eksikler loglarda bildirilir. Hiç uygun dosya bulunamazsa veritabanı değiştirilmeden işlem durur.
- Başlangıç dönemi bitiş döneminden sonraysa komut herhangi bir işe başlamadan hata verir.
- İndirilen dosyalar ve veri tabanı yedekleri otomatik silinmez. Disk alanını yönetmek için bunları kendiniz silebilirsiniz.

Bir yedeği geri yüklemek için önce veritabanını kullanan Jupyter oturumlarını ve diğer uygulamaları kapatın. Ardından `database_backups/` içindeki geri yüklemek istediğiniz dosyanın tam adını kullanın:

```bash
python pipeline.py restore-backup --backup-file database_backups/YEDEK_DOSYASININ_TAM_ADI.db --confirm
```

Geri yükleme öncesinde şu anki veritabanı da yeni bir yedek olarak saklanır.

## Veri kaynakları ve dikkat edilmesi gerekenler

- Finansal tablolar KAP'ta yayımlanan şirket raporlarından alınır.
- Enflasyon verileri TCMB'nin tüketici fiyatları verilerinden alınır.
- USD/TRY referans verileri Frankfurter servisinden alınır ve aylık değerlere dönüştürülür.
- Günlük hisse fiyatı verileri İş Yatırım'dan alınır. Tekrar çalıştırma yalnızca seçilen şirketleri ve tarih aralığını yeniler.
- Bazı finansal tablolar KAP'ta çeyrek içi değerler yerine tüm yıl için kümülatif değerler ile sunulur. Proje, uygun gelir tablosu değerlerini tek çeyrek değerine dönüştürür.
- Kaynak raporların biçimi değişebilir; otomatik okuma her kalemi kusursuz tanımayabilir. Veritabanı analiz içindir, resmi raporun yerine geçmez.

## Proje yapısı

```text
data_mappings/          Şirket listesi ve rapor başlık eşleştirmeleri
database_processing/    Enflasyon düzeltmesi, TTM, oranlar ve değişimler
models/                 Şirket ve finansal tablo veri yapıları
read_statements/        Rapor arşivlerini bulma, okuma ve kaydetme
services/               İndirme ve referans verisi servisleri
tests/                  Otomatik kontroller
pipeline.py             Komut satırı giriş noktası
bist_data.db            Oluşturulan yerel veritabanı
```

## Geliştirici notu

Testleri çalıştırmak için proje klasöründe `python -m pytest` komutunu kullanın. Kod biçimi ve yaygın hatalar için `ruff check .` kullanılabilir.

---

<a id="english"></a>

# BIST Financial Data Pipeline

[Türkçe](#turkce) | **English**

## At a glance

This project turns financial statements published by BIST companies on KAP into an organized dataset that is easier to compare across reporting periods. Instead of searching through separate reports one by one, you get a consistent starting point for exploring a company’s financial position and how it changes over time.

The project processes income statements, balance sheets, cash flow statements, and statements of changes in equity. It also calculates inflation-adjusted values, USD equivalents, totals for the most recent four quarters, and financial ratios. You can explore the results in a Jupyter notebook, SQLite, or a compatible reporting tool.

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
- Connect the SQLite database to a Jupyter notebook or compatible business-intelligence tool.

## Outputs

The main output is `bist_data.db` in the project folder, a local database whose tables you can query. Its main tables are:

| Table | Contents |
| --- | --- |
| `income_statement` | Income statement items and derived period measures, including recent-four-quarter totals |
| `balance_sheet` | Balance sheet items |
| `cash_flow` | Cash flow items |
| `equity_change` | Changes in equity |
| `ratios` | Calculated financial ratios |
| `inflation_table` | Monthly consumer inflation data |
| `usd_try_table` | Monthly USD/TRY reference rates |
| `prices` | Daily prices, trading volume, market value, and inflation-adjusted closing prices |

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

Once the environment is ready, download and process THYAO, TUPRS, and BIMAS from 2017 Q1 through 2026 Q2:

```bash
python pipeline.py run --symbols THYAO TUPRS BIMAS --begin-year 2017 --begin-quarter 1 --end-year 2026 --end-quarter 2
```

This refreshes reference data, downloads KAP archives, and processes the selected periods into the database. The inflation target defaults to the latest month stored in the database. You can then open `bist_data.db` in a Jupyter notebook or SQLite viewer.

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

- Before updating the database, the pipeline creates a verified snapshot in `database_backups/`. Its filename includes the backup time.
- If a local report is missing for a requested company-period, existing database rows for that period are preserved and the gap is logged. If no matching files are found at all, the command stops without changing the database.
- Reversed start and end periods are rejected before work begins.
- Backups are not automatically deleted. Remove old snapshots yourself when you are sure you no longer need them.

To restore a snapshot, first close Jupyter sessions and other applications connected to the database. Then use the exact filename from `database_backups/`:

```bash
python pipeline.py restore-backup --backup-file database_backups/EXACT_BACKUP_FILENAME.db --confirm
```

The current database is saved as another snapshot before the selected one is restored.

## Data sources and limitations

- Financial statements come from company reports published on KAP.
- Inflation data comes from the Central Bank of the Republic of Türkiye (TCMB) consumer price data.
- USD/TRY reference data comes from the Frankfurter service and is summarized into monthly values.
- Daily price data comes from Is Yatirim. Each company is saved separately, so completed companies remain in the database if a later company fails. Re-running replaces only the selected symbols and date range.
- Some KAP files show year-to-date cumulative figures rather than standalone quarterly values. The project converts applicable income statement values into standalone quarters.
- KAP report layouts can change, and automatic parsing may not recognize every item perfectly. The database supports analysis; it does not replace the official reports.
- Inflation and exchange-rate adjustments are limited by the source frequency and monthly reference method. They do not represent a company’s actual foreign-currency transactions or the inflation applicable on each item’s transaction date.

## Project layout

```text
data_mappings/          Company list and statement-label mappings
database_processing/    Inflation adjustment, TTM, ratios, and changes
models/                 Company and financial-statement data structures
read_statements/        Finding, reading, and saving report archives
services/               Download and reference-data services
tests/                  Automated checks
pipeline.py             Command-line entry point
bist_data.db            Generated local database
```

## For contributors

Run the tests from the project folder with `python -m pytest`. Run `ruff check .` for code-quality checks.
