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
| `silver.eps_observations` | Kaynağa bağlı EPS ve dönemsel ağırlıklı ortalama hisse sayıları |
| `gold.financial_values` | Çeyreklik, yılbaşından bugüne (YTD), enflasyona göre düzeltilmiş ve USD değerleri |
| `gold.financial_ratios` | Çeyreklik, YTD ve uygun oranlar için son 12 aylık (TTM) değerler |
| `gold.price_series` | Enflasyona göre düzeltilmiş günlük kapanışlar |
| `gold.current_eps_values` | Temel/sulandırılmış EPS, kapsam ve hesaplama kaynakları |

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

### Kümülatif nakit akışları ve eski kayıtların onarımı

Altı ve dokuz aylık nakit akışları Silver katmanında gerçek mali çeyrekleriyle, dönem tarihleriyle ve `is_cumulative` bilgisiyle tutulur. Gold çeyreklik akışı önceki kümülatif dönemden fark alarak hesaplar; gerekli önceki dönem yoksa çeyreklik tutar boş kalır, raporlanan YTD tutarı korunur. Dönem başı/sonu nakit bakiyeleri fark alınmadan saklanır. Enflasyon düzeltmeli raporlar arasında fark alınırken önceki YTD, mevcut raporun satın alma gücüne çevrilir. Nominal/enflasyon düzeltmeli baz geçişlerinde veya baz bilinmiyorsa çeyrek türetilmez.

Önceki sürümün Q2/Q3 nakit akışlarını Q4 olarak etiketlediği kayıtları, saklanan Bronze verilerinden indirme yapmadan onarmak için:

```bash
.venv/bin/python pipeline.py repair-cash-flows
```

`--symbols THYAO TUPRS BIMAS` ile kapsamı daraltabilirsiniz. Komut yalnızca nakit akışı Silver kayıtlarını yeniden yayımlar; özgün Bronze ve önceki Silver sürümlerini korur. Halihazırda Gold verisi yayımlanan şirketlerin hesaplamalarını mevcut enflasyon hedefleriyle yeniler. Gold verisi henüz yayımlanmamış şirketleri kendiliğinden yayımlamaz.

## PostgreSQL finansal veri akışı

`run` ve `update-database`, yerel tabloları varsayılan olarak iki ayrı süreçte ayrıştırır. `--parse-workers 4` ile artırabilir veya `--parse-workers 1` ile seri çalıştırabilirsiniz. Bekleyen işler işçi başına ikiyle sınırlıdır; veritabanı yazımları ana süreçte seri yapılır. Bu seçenek KAP istek aralığını değiştirmez. EPS çıkarımı ayrı ve sınırlı işçilerle yapılır (varsayılan iki); Gold hesaplamaları seri devam eder. Tüm ayrıştırılmış tablolar bellekte tutulduğu için daha fazla işçi bellek kullanımını artırabilir.

Şirket metaverisi PostgreSQL'de tutuluyor. Finansal veriler için eklenen medallion akışı KAP kaynaklarını `bronze` altında korur, seçilen ve temizlenen kayıtları `silver` altında yayımlar, çeyreklik değerleri, enflasyon düzeltmelerini ve TTM dahil finansal oranları `gold` altında üretir. Her çalıştırma ve kalite kontrol sonucu `ops` şemasında izlenir. Yeni hesaplamalar ayrı sürüm olarak saklanır; `gold.current_financial_values`, `gold.current_financial_ratios` ve `gold.current_price_series` görünümleri en son yayımlanan sürümü gösterir.

Tüm finansal ve fiyat verileri PostgreSQL'e yazılır. Ana CLI komutları PostgreSQL kullanır:

```bash
python pipeline.py migrate-postgres
python pipeline.py update-reference-data
python pipeline.py update-database --symbols THYAO TUPRS BIMAS --begin-year 2017 --begin-quarter 1 --end-year 2026 --end-quarter 2
python pipeline.py update-prices --symbols THYAO TUPRS BIMAS --begin-date 2017-01-01 --end-date 2026-06-30
python pipeline.py adjust-prices --price-level-year 2026 --price-level-month 6
```

İndirme ve finansal tabloları PostgreSQL'e işleme adımlarını tek seferde çalıştırmak için `run` komutunu kullanın. Hisse fiyatları ayrı bir komutla alınır. Önce `BIST_FINANCE_DSN` değişkeninin `bist_finance` veritabanına bağlandığını doğrulayın. Tabloları Jupyter'dan aynı PostgreSQL bağlantısıyla sorgulayabilirsiniz.

### TTM oranlarının hesaplanması

TTM alanları yalnızca `gold.financial_ratios` tablosunda tutulur. Oran hesaplaması gerekli gelir tablosu tutarlarını ortak enflasyon bazındaki `value_real` değerlerinden toplar; her girdi için ardışık dört çeyrek gerekir. Marjlar çeyreklik oranların ortalaması yerine toplam karın toplam hasılata bölünmesiyle hesaplanır. TTM aktif karlılığı, özkaynak karlılığı ve aktif devir hızı dönem başı ve sonu bakiyelerinin ortalamasını kullanır; dönem başı bakiye yoksa sonuç boş bırakılır. Cari oran, nakit oranı ve borçluluk oranlarının TTM alanları boştur.

EPS ayrı kaynak verilerinden hesaplanır; güncel hisse sayısı veya ödenmiş sermaye geçmiş dönemler için kullanılmaz. Ortak TÜFE bazına dönüşüm, kaynak raporlardaki yeniden düzenleme ve enflasyon muhasebesi farklılıklarını tek başına çözmez.

`python pipeline.py migrate-postgres` komutu 008 geçişiyle finansal değerlerdeki eski TTM sütunlarını kaldırır; oran sütunları korunur. Mevcut Silver verilerinden oranları yeniden üretmek için `python pipeline.py calculate-gold` kullanın. Bu komut bekleyen geçişleri de uygular; rapor indirmez veya yeniden okumaz.

### Rapor dönemleri ve ölçüm bazları

Gelir ve nakit akışlarında raporlanan çeyrek/YTD tutarları birlikte korunur. PDF başlığı, kaynak özeti, nominal/enflasyon düzeltmeli/bilinmeyen ölçüm bazı ve satın alma gücü tarihi `silver.financial_report_context` içinde tutulur. Fark alınmadan önce uyumlu enflasyon düzeltmeli tutarlar aynı satın alma gücüne çevrilir; nominal/enflasyon düzeltmeli geçişlerde çeyrek tahmin edilmez. Gold gerçek dönem tarihlerini, `value_real_ytd` tutarını ve hesaplama açıklamalarını saklar. TÜFE dönüşümü için aradaki ayların tamamı gerekir; bu analitik dönüşüm nominal hesapların TMS 29 yeniden düzenlemesini üretmez.

TÜFE ve kur eşleşmesi gerçek rapor bitiş ayını kullanır. USD tutarı ayın açılış referans kuruyla analitik karşılıktır; muhasebesel kur çevrimi değildir. Büyüme oranlarında dönem atlanmaz. Kaynakları düzeltmek için `.venv/bin/python pipeline.py repair-financials` çalıştırın; varsayılan olarak aktif Silver şirketleri onarılır, yalnızca önceden yayımlanmış Gold şirketleri yenilenir. Önceki sürümler ve mevcut enflasyon hedefi korunur; indirme yapılmaz.

### Hisse başına kazanç (EPS)

EPS, `TRY_per_1_TRY_nominal` birimine dönüştürülür: 1 TL nominal pay tutarı başına TL. Kaynaktaki kuruş/TL, nominal pay değeri, bin/milyon para birimi ve hisse sayısı çarpanları ayrı saklanır. Örneğin 0,01 TL nominal pay için kuruş cinsinden açıklanan değer, TL cinsinden ve 1 TL nominal pay bazındaki değerle doğrudan aynı birim değildir.

`eps` toplam temel EPS, `eps_diluted` toplam sulandırılmış EPS'dir. Sürdürülen faaliyetler ayrı `eps_continuing` ve `eps_continuing_diluted` kodlarında tutulur. Pay sınıfları `gold.current_eps_values` içinde ayrı saklanır. Kaynak raporda açıklanan EPS önceliklidir; kazanç/pay sayısı girdileri varsa tutarlılığı da kontrol edilir. Yalnızca ana ortaklığın adi pay sahiplerine ait kazanç ve ilgili dönemin ağırlıklı ortalama pay sayısı kullanılır. Eksik veya belirsiz girdilerde sonuç boş kalır.

Çeyreklik EPS, YTD EPS farkı alınarak hesaplanmaz. Doğrulanmış ortak pay/enflasyon bazındaki YTD kazanç ve gün ağırlıklı pay sayılarından türetilebilir. TTM EPS dört ardışık çeyreğin kazancını on iki aylık gün ağırlıklı pay sayısına böler; doğrulanmış tam yıllık rapor Q4 için doğrudan TTM kaynağı olabilir. Farklı bedelsiz artırım/bölünme veya satın alma gücü bazları otomatik birleştirilmez. EPS değişim oranları için de doğrulanmış ortak baz gerekir. Raporda enflasyon muhasebesi uygulanmış olması korunur; ayrıca TÜFE ile düzeltilmiş EPS adı altında yeniden etiketlenmez.

Yerel KAP PDF'lerinden geçmiş EPS verilerini almak ve mevcut Gold şirketlerini yenilemek için:

```bash
.venv/bin/python pipeline.py update-eps --symbols THYAO TUPRS BIMAS
```

Şirket verilmezse yalnızca Gold verisi yayımlanmış şirketler seçilir. Normal `run` ve `update-database` akışları da EPS kaynaklarını tarar. Tanınmayan PDF düzenleri ve birimler `ops.data_quality_results` içinde bildirilir. İndirme yapılmaz. Poppler `pdftotext` varsa kullanılır; yoksa `pypdf` kullanılır.

Doğrulanmış tarihsel girdiler JSON ile de aktarılabilir: `.venv/bin/python pipeline.py import-eps --file verified-eps.json`. Aşağıdaki İngilizce bölümde dosya biçimi verilmiştir; güncel şirket metaverisinden tahmini hisse sayıları bu dosyaya yazılmamalıdır.

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
| `silver.eps_observations` | Reported EPS and period-specific weighted-average share inputs |
| `gold.financial_values` | Quarterly, year-to-date (YTD), inflation-adjusted, and USD values |
| `gold.financial_ratios` | Quarterly, YTD, and applicable trailing-twelve-month (TTM) ratios |
| `gold.price_series` | Inflation-adjusted daily closing prices |
| `gold.current_eps_values` | Basic/diluted EPS, earnings scope, and calculation provenance |

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

### Cumulative cash flows and repairing older records

Six- and nine-month cash flows retain their actual fiscal quarter, period dates, and `is_cumulative` flag in Silver. Gold derives standalone flows by subtracting the preceding cumulative period. If the required prior period is missing, the quarterly amount remains null while the reported YTD amount is retained. Opening and closing cash balances remain balances rather than being differenced or summed. When both reports use inflation-adjusted units, prior YTD is converted to the current report’s purchasing power before subtraction. Unknown bases and nominal-to-inflation-adjusted transitions do not produce derived quarters.

To repair records that the older version incorrectly labeled Q4, rebuild cash-flow observations from retained Bronze data without downloading reports:

```bash
.venv/bin/python pipeline.py repair-cash-flows
```

Add `--symbols THYAO TUPRS BIMAS` to limit the scope. The command republishes only cash-flow Silver observations and retains original Bronze filings and previous Silver versions. It recalculates already-published Gold companies using their existing inflation targets, without publishing companies whose Gold data was not previously active.

## PostgreSQL financial pipeline

### Quarter-end price/earnings

`calculate-pe` refreshes P/E from published Gold basic total TTM EPS and stored Is Yatirim original prices, without downloading or rebuilding financial statements. Use `--symbols THYAO TUPRS` to limit companies. Normal Gold calculations also refresh P/E automatically.

```bash
.venv/bin/python pipeline.py calculate-pe
```

The price is the last available trading close on or before the **actual fiscal period end**, within ten calendar days. It uses original `HG_KAPANIS` (`at_the_date_close_price`) retained in Bronze, rather than the corporate-action-adjusted `HGDG_KAPANIS` stored as `silver.daily_prices.close`. P/E is original TRY close divided by verified basic total TTM EPS in TRY per 1 TRY nominal capital. Quarterly/YTD EPS is never annualized, and P/E is null for nonpositive earnings, missing original prices, unsupported split or purchasing-power bases, or ambiguous multiple share classes. Missing inputs are never treated as zero.

`gold.current_financial_ratios` exposes `ratio_code='price_earnings'` in `value_ttm`; `value_quarter` and `value_ytd` are null. `gold.current_price_earnings` exposes price date, original close, EPS, source-price row ID, financial/valuation run IDs, and calculation details including null reasons. The historical ratio is **retrospective**: the quarter's EPS may have been published after the selected price date. It is not a publication-aware backtesting dataset. A new standalone valuation run preserves prior valuation snapshots. Rerun `calculate-pe` after updating prices if financials have not been rebuilt.

`run` and `update-database` parse local statements using two worker processes by default. Set `--parse-workers 4` to increase this or `--parse-workers 1` for serial parsing. Pending work is capped at two files per worker; database writes stay serial in the main process. This option does not change KAP request pacing. EPS extraction uses separate bounded workers (default: two); Gold calculations remain serial. All parsed frames are retained in memory, so additional workers can increase memory use.

Company metadata already lives in PostgreSQL. The new medallion path keeps KAP source records in `bronze`, publishes selected and cleaned observations in `silver`, and builds quarterly values, inflation adjustments, and financial ratios including TTM ratios in `gold`. Run history and data-quality checks are tracked in `ops`. Each calculation is stored as a separate generation; `gold.current_financial_values`, `gold.current_financial_ratios`, and `gold.current_price_series` expose the latest published generation.

All financial and price data is stored in PostgreSQL. The primary commands are:

```bash
python pipeline.py migrate-postgres
python pipeline.py update-reference-data
python pipeline.py update-database --symbols THYAO TUPRS BIMAS --begin-year 2017 --begin-quarter 1 --end-year 2026 --end-quarter 2
python pipeline.py update-prices --symbols THYAO TUPRS BIMAS --begin-date 2017-01-01 --end-date 2026-06-30
python pipeline.py adjust-prices --price-level-year 2026 --price-level-month 6
```

Use `run` to download and process financial statements in one run. Historical prices are fetched separately. Confirm that `BIST_FINANCE_DSN` points to the `bist_finance` database before running these commands. Query the tables from Jupyter using the same PostgreSQL connection settings.

### TTM ratio calculations

TTM fields live only in `gold.financial_ratios`. The ratio calculation sums the required income-statement inputs using `value_real` at a common CPI base, requiring four consecutive, non-null quarters for each input. Margins divide trailing profit by trailing revenue rather than averaging quarterly ratios. TTM ROA, ROE, and asset turnover use the average of the opening and closing balances; missing opening balances produce null results. Current, cash, debt, and debt-to-equity ratios have no TTM value.

EPS uses separate source disclosures; current shares and paid-in capital are never used as historical denominators. Normalizing monetary amounts to a common CPI base does not by itself reconcile restatements or differing inflation-accounting bases in the source reports.

Run `python pipeline.py migrate-postgres` to apply migration 008, which removes the old TTM columns from financial values while preserving ratio columns. Rebuild ratios from existing Silver data with `python pipeline.py calculate-gold`. That command also applies pending migrations and does not download or reread statements.

### Reported periods and measurement bases

Both reported quarterly and YTD income/cash-flow amounts are retained in Silver. `silver.financial_report_context` records the PDF presentation header, source hash, nominal/inflation-adjusted/unknown basis, and purchasing-power date. Gold preserves reported YTD instead of adding quarters at different price levels. Derived quarters and YTD totals require compatible scopes and bases; unknown or changed bases produce nulls. Reported full-year YTD can directly supply Q4 trailing ratio inputs even when Q4 itself cannot be derived safely.

Gold records actual quarter dates, basis, `value_real_ytd`, and calculation details. CPI transformations require every intervening month. These transformations are analytical comparisons; they do not reconstruct IAS 29 restatements of nominal accounts. CPI/FX lookup uses the actual reporting end month, including non-calendar years. `value_usd` uses that month’s opening USD/TRY reference rate, matched by year/month regardless of its trading date; it is an analytical equivalent, not an IAS 21 accounting translation. Growth requires the exact preceding quarter or prior-year quarter and compatible calendar spacing. Non-EPS TTM requires four contiguous quarters spanning a full year, or a verified reported full year at Q4.

To restore retained Bronze sources for all active Silver companies and refresh only already-published Gold companies, run:

```bash
.venv/bin/python pipeline.py repair-financials
# Or limit the repair:
.venv/bin/python pipeline.py repair-financials --symbols THYAO TUPRS BIMAS
```

The repair preserves prior source/processing versions and existing inflation targets. It does not download files. Missing or ambiguous PDF presentation headers are recorded in data-quality results; affected reported amounts remain available while unsupported derived/real amounts stay null. Regular statement ingestion records the same contexts.

### Earnings per share (EPS)

EPS is normalized to `TRY_per_1_TRY_nominal`: TRY per 1 TRY nominal share capital. Original kuruş/TRY units, nominal share value, monetary multipliers, share-count multipliers, and explicit split factors are preserved separately. A kuruş figure for a 0.01 TRY nominal share must not be treated as TRY EPS for a 1 TRY nominal share without normalization.

`eps` is total basic EPS; `eps_diluted` is total diluted EPS. Continuing operations use separate `eps_continuing` and `eps_continuing_diluted` codes. Share classes remain separate in `gold.current_eps_values`. Reported EPS takes priority and is reconciled to disclosed earnings and weighted-average shares when available. Only earnings attributable to ordinary shareholders of the parent and period-specific share counts qualify for verified EPS. Missing or ambiguous inputs produce nulls in verified EPS; historical-capital estimates are exposed separately in the coverage view described below.

Quarterly EPS is not obtained by subtracting YTD EPS. It can be derived from YTD attributable earnings and day-weighted shares only with verified common share and purchasing-power bases. TTM EPS divides four consecutive quarters of attributable earnings by the twelve-month day-weighted share count. A verified full-year report can directly supply Q4 TTM EPS. Splits and bonus issues are not guessed; inflation-adjusted profits can be reconciled using complete monthly CPI spans, with every conversion recorded. EPS growth also requires a verified common basis. Reported period EPS is preserved; derived CPI-based TTM uses a distinct calculation method in its provenance.

Backfill from local KAP PDFs and refresh existing Gold companies:

```bash
.venv/bin/python pipeline.py update-eps --symbols THYAO TUPRS BIMAS
```

Omitting symbols selects only companies with published Gold data. Normal `run` and `update-database` processing also scans EPS sources. Unsupported layouts/units are recorded in `ops.data_quality_results`. No reports are downloaded. Text extraction uses Poppler `pdftotext` if available and otherwise `pypdf`.

The EPS reader also extracts KAP HTML `new-type-row` cells, mapping each numeric cell to its own date header while preserving blank positions. PDF notes verify currency/nominal-share units and reconcile disclosed earnings with historical weighted-average shares; the financial statement monetary multiplier is never applied to EPS. Wrapped PDF dates, alternate note headings, and explicit or uniquely reconciled share-count scales are supported. Unresolved units, source disagreements, and unsupported layouts remain unavailable with diagnostic reasons. Automatic ingestion preserves verified JSON inputs and compatible existing observations.

`update-eps --workers 4` refreshes local EPS using four bounded extraction workers, then atomically refreshes EPS, coverage and P/E against existing published financial datasets. Workers do no database writes; the default is two workers. Structured source amounts, date columns, PDF evidence, and normalization details are retained in `bronze.eps_sources.source_payload` and `silver.eps_observations.normalization`.

`.venv/bin/python pipeline.py calculate-eps` repeats only the derived EPS/coverage/P/E refresh using stored sources and prices, with optional `--symbols MAVI`. It does not re-read files or rebuild financial values. The financial dataset run ID remains unchanged; the actual EPS refresh run is recorded as `calculation_details.eps_refresh_run_id`. Replaced EPS/coverage/ratio rows are retained in `ops.eps_refresh_snapshots`; P/E valuation history remains versioned. A failed refresh rolls back all its derived changes and snapshots. Normal full Gold calculations also calculate EPS coverage automatically.

The recovery reader accepts explicitly declared EPS currency/nominal units independently of calculation-note extraction, preserves blank cells in mixed-currency PDF tables, and calculates basic EPS from independently disclosed parent-attributable earnings and weighted-average shares even when only diluted EPS is printed. It supports Roman-numbered note headings. Optional `update-eps --ocr` processes image-only pages locally using `pdftoppm` and `tesseract` (install Turkish language data for best results); OCR numbers still pass the same date, unit and reconciliation checks. Unknown units or actual source contradictions are never filled by assumption.

EPS-note reporting units take precedence over legal-share nominal values in capital notes. For example a 0.01 TRY legal share can coexist with EPS stated per 1 TRY nominal capital; applying the legal-share conversion to that EPS produces a 100-fold error. Direct reported-value recovery therefore requires an EPS-local nominal declaration, including attached note footnotes, and keeps neighboring basic/diluted currency labels separate.

Gold also builds `gold.current_eps_coverage`, one row per company/fiscal period. Its `verified_value_quarter/ytd/ttm` fields remain source-verified; `estimated_value_quarter/ytd/ttm` use parent-attributable earnings divided by calendar-day-weighted historical **issued nominal capital** from İş Yatırım. The individual legal-share nominal value cancels in our TRY-per-1-TRY-nominal output unit. Estimates do not deduct unknown treasury shares, infer preference rights, or retrospectively classify bonus/rights issues; these assumptions, capital-history hashes, calculation evidence, and missing-input reasons are retained in `assessment`. Estimates can carry the first observed historical capital back by up to seven opening calendar days (for example January 1 before the first stored trading day); such cases have an explicit `opening_capital_assumption` in the evidence and never establish a verified share basis. Multiple instruments prevent estimation; later capital gaps over ten days prevent price-based weighting and can trigger the explicit reported-capital proxy below. No current-company-capital substitution is made.

If price-feed capital history is unavailable or stale, a uniquely reported positive `ödenmiş sermaye` value from the same period's balance sheet can supply a **period-end paid-capital proxy**, explicitly identified by `assessment.evidence.<period>.denominator_method='period_end_reported_paid_in_capital_proxy'`. This does not establish a weighted-average denominator, treasury adjustment, or verified nominal/legal interpretation of an inflation-accounting balance. Known multiple instruments still prevent this proxy. For older financial-company templates, standalone net profit is usable only when the linked source filing explicitly says `Konsolide Olmayan`; consolidated bank group profit additionally reconciles to net profit less minority interests. These numerator rules do not substitute comprehensive income.

An explicitly zero income-statement minority-profit component can also establish that total net profit equals parent profit. Missing minority rows are never treated as zero; contradictory minority rows block this inference. A bank group-profit row can use this rule when the net-profit row is absent, but an available contradictory net/group/minority reconciliation is never bypassed.

When an older consolidated template omits parent profit, the coverage estimate can reconstruct it as net profit minus an explicitly disclosed minority-profit component. Both inputs must be unique, have matching periods and measurement bases, and share a source filing; an existing contradictory explicit parent/group row prevents this reconstruction. Both input financial-value IDs are recorded in the estimate evidence.

For Power BI, `best_available_quarter/ytd/ttm` prefer verified values over estimates, and the matching `quarter_quality/ytd_quality/ttm_quality` columns explicitly say `verified`, `estimated`, or `review_required`. Keep these quality fields visible when using best-available values. `gold.eps_review_queue` lists periods requiring estimates or further review. Estimates never enter verified EPS ratios or verified P/E. Negative EPS remains available even though P/E is suppressed for nonpositive earnings.

Disclosed historical outstanding-share denominators can acquire a common share basis when dated İş Yatırım capital confirms a stable issued-capital regime across their full periods and the counts reconcile to issued capital. Capital changes require independently verified corporate-action treatment (or the existing verified JSON import); no arbitrary common basis identifier is assigned. Verified four-quarter TTM calculations convert inflation-adjusted profits to the final period's purchasing power using complete monthly CPI spans before dividing by the day-weighted denominator. Nominal/inflation-adjusted transitions remain unsupported. Early history may require preceding-year reports and opening capital; source absence is recorded rather than guessed.

Verified inputs can also be imported with `.venv/bin/python pipeline.py import-eps --file verified-eps.json`. This synthetic example uses a placeholder company symbol; replace all values with verified disclosures:

```json
{
  "observations": [{
    "symbol": "EXAMPLE",
    "fiscal_year": 2024,
    "fiscal_quarter": 1,
    "period_start": "2024-01-01",
    "period_end": "2024-03-31",
    "period_kind": "ytd",
    "eps_kind": "basic",
    "earnings_scope": "total",
    "share_class": "ordinary",
    "reported_eps": "2.00",
    "attributable_profit": "200000000",
    "weighted_average_shares": "100000000",
    "eps_unit": "TRY",
    "money_multiplier": "1",
    "shares_multiplier": "1",
    "share_nominal_value": "1",
    "split_adjustment_factor": "1",
    "share_basis_id": null,
    "purchasing_power_date": null,
    "rounding_tolerance": "0.005"
  }]
}
```

`share_basis_id` must remain null unless historical corporate actions have been verified and the inputs normalized to a common split-adjusted basis. A `split_adjustment_factor` of 2 doubles the adjusted share count and halves EPS, leaving earnings unchanged. `purchasing_power_date` identifies the actual source-report price basis; do not set differing bases equal merely to enable a calculation. Raw and normalized observations and prior source versions are retained for audit.

Each imported company/fiscal-period group replaces its active EPS source as a whole. Include every basic/diluted, quarterly/YTD, share-class and earnings-scope observation that should remain active for that period.

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

PostgreSQL integration tests use a disposable database and require a role with `CREATEDB`. Set `BIST_TEST_POSTGRES_DSN` to the connection details for that server, then run `python -m pytest`. Without that variable, database integration tests are skipped. The supplied database is never migrated or recalculated by these tests.
