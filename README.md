<a id="turkce"></a>

# BIST Şirketleri İçin Finansal Veri Pipeline'ı

**Türkçe** | [English](#english)

## Kısaca

BIST şirketlerinin KAP'ta yayımlanan finansal tablolarını indirip düzenli, dönemler arasında karşılaştırılabilir bir veri setine dönüştüren bir projedir. Amaç, farklı raporlardaki dağınık bilgileri tek tek aramak yerine şirketlerin finansal durumunu ve zaman içindeki değişimini inceleyebileceğiniz bir temel sunmaktır.

Proje gelir tablolarını, bilançoları, nakit akış tablolarını ve özkaynak değişim tablolarını işler. TÜFE'ye göre düzeltilmiş değerler, USD karşılıkları, hisse başına kazanç ve son 12 ayı kapsayan finansal oranlar üretir. Sonuçları PostgreSQL, Jupyter Notebook veya uyumlu bir raporlama aracıyla inceleyebilirsiniz.

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
- USD karşılıklarını, son 12 aylık (TTM) oranları, hisse başına kazancı (EPS) ve dönem sonu fiyat/kazanç (F/K) oranını sorgulayabilirsiniz.
- Hazırlanan verileri kendi şirket karşılaştırmalarınızda, grafiklerinizde ve araştırmalarınızda kullanabilirsiniz.
- PostgreSQL veritabanını Jupyter Notebook'a veya uyumlu bir iş zekası aracına (Power BI, Tableau gibi) bağlayabilirsiniz.

## Üretilen çıktılar

Ana çıktı `bist_finance` PostgreSQL veritabanıdır. Raporlama için Gold katmanının `current_*` görünümleri, kaynak ve hesaplama denetimi için Bronze, Silver ve Ops kayıtları kullanılabilir.

| Tablo veya görünüm | İçerik |
| --- | --- |
| `metadata.companies` ve `metadata.instruments` | Şirket ve işlem gören pay kimlikleri |
| `bronze.statement_filings` ve `bronze.statement_rows` | KAP dosyalarından alınan kaynak kayıtlar |
| `silver.current_financial_observations` ve `silver.financial_report_context` | Etkin finansal tablo satırları, mali dönem ve ölçüm bazı |
| `silver.monthly_macro_data` ve `silver.daily_prices` | Aylık TÜFE ve USD/TRY, günlük fiyatlar ve tarihsel sermaye |
| `silver.current_eps_observations` | Etkin EPS açıklamaları ve dönemsel ağırlıklı ortalama pay sayıları |
| `gold.current_financial_values` | Çeyreklik, mali yılbaşından itibaren kümülatif (YTD), TÜFE'ye göre düzeltilmiş ve USD değerleri |
| `gold.current_financial_ratios` | Çeyreklik, YTD ve TTM oranları, `eps`, `eps_inflation_adj` ve doğrulanmış F/K |
| `gold.current_price_series` | Yayımlanmış günlük kapanışlar ve TÜFE'ye göre düzeltilmiş karşılıkları |
| `gold.current_eps_values` ve `gold.current_eps_coverage` | Ayrıntılı doğrulanmış EPS, sermaye bazlı tahminler ve alan bazında kalite bilgisi |
| `gold.current_price_earnings` | Dönem sonu doğrulanmış ve tahmini F/K, fiyat/EPS kaynakları ve kalite bilgisi |
| `ops.pipeline_runs` ve `ops.data_quality_results` | Çalıştırma durumları ve veri kalitesi kayıtları |

Finansal tablolardaki kalemler; şirket, yıl, çeyrek, kalem adı ve mümkün olduğunda rapordaki üst başlıklarıyla birlikte saklanır. Böylece aynı ada sahip kalemlerin farklı bölümlerde yer alması daha kolay ayırt edilir. Ayrıca nominal tutarlar, enflasyona göre düzeltilmiş tutarlar ve USD karşılıkları ayrı alanlarda bulunur.

## Hızlı başlangıç

Python 3.11+ ve çalışan bir PostgreSQL 15+ sunucusu gerekir.

### Windows

PowerShell'i proje klasöründe açın ve çalıştırın:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

PowerShell sanal ortamı etkinleştirmeyi engellerse, `.\.venv\Scripts\python.exe` yolunu `python` yerine kullanabilirsiniz.

### macOS ve Linux

Terminal'i proje klasöründe açın ve çalıştırın:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### PostgreSQL kurulumu

Bağlantıda `YOUR_USER` yerine PostgreSQL kullanıcınızı yazın. macOS veya Linux'ta bağlantıyı kabukta tanımlayın.

```bash
export BIST_FINANCE_DSN="dbname=bist_finance host=localhost user=YOUR_USER"
```

PowerShell'de aşağıdaki komutu kullanın.

```powershell
$env:BIST_FINANCE_DSN = "dbname=bist_finance host=localhost user=YOUR_USER"
```

Bağlantı sırası `BIST_FINANCE_DSN`, `DATABASE_URL` ve yerel `dbname=bist_finance` varsayılanıdır. Bağlanılan veritabanının adı `bist_finance` olmalıdır.

```sh
python pipeline.py init-postgres --create-database
```

Komut gerekirse `bist_finance` veritabanını oluşturur ve [depodaki SQL geçişlerini](database/migrations/) uygulayarak metaveri, Bronze, Silver, Gold ve Ops şemalarını kurar. Metaveri tabloları boşsa depodaki JSON kayıtlarını yükler. Yeniden çalıştırıldığında mevcut şirket metaverisini korur.

`--create-database` için kullanıcının `postgres` veritabanına bağlanabilmesi, veritabanı henüz yoksa da `CREATEDB` yetkisine sahip olması gerekir. `bist_finance` önceden oluşturulmuşsa şema ve tabloları oluşturma yetkisi olan bir kullanıcıyla `python pipeline.py init-postgres` çalıştırın.

Kurulu bir şemayı güncellemek için `migrate-postgres` kullanın. Ardından yayımlanmış dönemler için `eps_inflation_adj` ve tahmini F/K'yı hesaplamak üzere `calculate-eps` çalıştırın.

### İlk finansal veri çalıştırması

Üç şirketin 2017 Q1 ile 2026 Q2 arasındaki verilerini indirin ve işleyin.

```bash
python pipeline.py run --symbols THYAO TUPRS BIMAS --begin-year 2017 --begin-quarter 1 --end-year 2026 --end-quarter 2
```

Komut referans verilerini yeniler, yıllık KAP arşivlerini `financial_statements/<SEMBOL>/<SEMBOL>_<YIL>.zip` altında saklar ve seçilen dönemleri yayımlar. TÜFE hedefi, veritabanındaki en güncel TÜFE ayıdır. Günlük fiyatları ayrıca `update-prices` ile alın ve F/K'yı `calculate-pe` ile yenileyin.

## İşlemleri ayrı ayrı çalıştırma

Her aşamayı ayrı çalıştırmak da mümkündür:

```bash
# Yalnızca KAP arşivlerini indir
python pipeline.py download --symbols THYAO TUPRS BIMAS --begin-year 2017 --end-year 2026

# Enflasyon ve USD/TRY referans tablolarını yenile
python pipeline.py update-reference-data

# Daha önce indirilmiş arşivleri okuyup veritabanını güncelle
python pipeline.py update-database --symbols THYAO TUPRS BIMAS --begin-year 2017 --begin-quarter 1 --end-year 2026 --end-quarter 2

# Mevcut Silver kayıtlarından Gold finansal verilerini yeniden hesapla
python pipeline.py calculate-gold --symbols THYAO TUPRS BIMAS

# Günlük hisse fiyatlarını indir
python pipeline.py update-prices --symbols THYAO TUPRS BIMAS --begin-date 2017-01-01 --end-date 2026-06-30

# Saklanan fiyatlarla dönem sonu F/K'yı yenile
python pipeline.py calculate-pe --symbols THYAO TUPRS BIMAS

# Fiyatları indirmeden enflasyon düzeltmesini yeniden hesapla
python pipeline.py adjust-prices --symbols THYAO TUPRS BIMAS --price-level-year 2026 --price-level-month 6

# KAP'tan mevcut şirket metaverisini güncelle
python -m services.update_company_info --symbols THYAO TUPRS BIMAS
```

`run` ile mevcut arşivleri veya referans verilerini kullanmak için sırasıyla `--skip-download` ve `--skip-reference-data` seçenekleri vardır. `run`, `download`, `update-database` ve `update-prices` komutlarında `--symbols` verilmezse metaverideki tüm şirketler seçilir. `calculate-gold` etkin Silver kayıtlarını, `calculate-pe` yayımlanmış Gold EPS kayıtlarını, `adjust-prices` ise saklanan fiyatları kullanır. Komut seçeneklerini görmek için `python pipeline.py <komut> --help` çalıştırın.

`run`, `update-database` ve `calculate-gold` için belirli bir TÜFE hedefi seçmek üzere `--price-level-year` ve `--price-level-month` seçeneklerini birlikte verin. Bu iki seçenek `update-prices` ile birlikte kullanıldığında Gold fiyat serisi de hesaplanır. `adjust-prices` her iki seçeneği de gerektirir.

## Veri güvenliği ve kapsam

- Finansal tablo ve EPS aktarımı ile Gold yayını ayrı işlemlerde kaydedilir. Gold hesabı başarısız olursa önceki Gold yayını etkin kalır.
- Eksik yerel raporlar, o dönemin etkin Silver kayıtlarını korur. Hiç uygun arşiv bulunamazsa finansal tablo aktarımı başlamadan durur. `run` bu aşamadan önce referans verilerini yenileyebilir.
- `run`, `download` ve `update-database` ters tarih aralıklarını işe başlamadan reddeder.
- KAP arşivleri otomatik silinmez; disk alanını yönetmek için bunları ayrıca temizleyebilirsiniz.

## Veri kaynakları ve dikkat edilmesi gerekenler

- Finansal tablolar KAP'ta yayımlanan şirket raporlarından alınır.
- Enflasyon verileri TCMB'nin tüketici fiyatları verilerinden alınır.
- USD/TRY referans verileri Frankfurter servisinden alınır ve aylık değerlere dönüştürülür.
- Günlük hisse fiyatı ve tarihsel sermaye verileri İş Yatırım'dan alınır. Fiyatlar her sembol için ayrı kaydedilir ve tekrar çalıştırıldığında kaynaktan dönen satırlar güncellenir.
- Bazı finansal tablolar KAP'ta çeyrek içi değerler yerine tüm yıl için kümülatif değerler ile sunulur. Proje, uygun gelir tablosu değerlerini tek çeyrek değerine dönüştürür.
- Kaynak raporların biçimi değişebilir; otomatik okuma her kalemi kusursuz tanımayabilir. Veritabanı analiz içindir, resmi raporun yerine geçmez.

### Kümülatif nakit akışları ve eski kayıtların onarımı

Altı ve dokuz aylık nakit akışları Silver katmanında gerçek mali çeyrekleriyle, dönem tarihleriyle ve `is_cumulative` bilgisiyle tutulur. Gold çeyreklik akışı önceki kümülatif dönemden fark alarak hesaplar. Gerekli önceki dönem yoksa çeyreklik tutar boş kalır ve raporlanan YTD tutarı korunur. Dönem başı ve sonu nakit bakiyeleri olduğu gibi saklanır. Enflasyon düzeltmeli raporlar arasında fark alınırken önceki YTD, mevcut raporun satın alma gücüne çevrilir. Çeyrek türetmek için ölçüm bazlarının biliniyor ve uyumlu olması gerekir.

Önceki sürümün Q2/Q3 nakit akışlarını Q4 olarak etiketlediği kayıtları, saklanan Bronze verilerinden indirme yapmadan onarmak için:

```bash
python pipeline.py repair-cash-flows
```

`--symbols THYAO TUPRS BIMAS` ile kapsamı daraltabilirsiniz. Komut yalnızca nakit akışı Silver kayıtlarını yeniden yayımlar; özgün Bronze ve önceki Silver sürümlerini korur. Halihazırda Gold verisi yayımlanan şirketlerin hesaplamalarını mevcut enflasyon hedefleriyle yeniler. Gold verisi henüz yayımlanmamış şirketleri kendiliğinden yayımlamaz.

## PostgreSQL finansal veri akışı

`run` ve `update-database`, yerel tabloları varsayılan olarak iki ayrı süreçte ayrıştırır. `--parse-workers 4` ile artırabilir veya `--parse-workers 1` ile seri çalıştırabilirsiniz. Bekleyen işler işçi başına ikiyle sınırlıdır; veritabanı yazımları ana süreçte seri yapılır. Bu seçenek KAP istek aralığını değiştirmez. EPS çıkarımı ayrı ve sınırlı işçilerle yapılır (varsayılan iki); Gold hesaplamaları seri devam eder. Tüm ayrıştırılmış tablolar bellekte tutulduğu için daha fazla işçi bellek kullanımını artırabilir.

Gold hesaplamaları çalıştırma kimliğiyle yayımlanır. Başarılı bir yeniden hesaplama, aynı dönemler için eski Gold finansal değerlerini ve oranlarını, aynı tarihler için de eski Gold fiyat serisi satırlarını temizler. Bronze kaynak ve Silver finansal tablo sürümleri, F/K hesaplama geçmişi ve EPS yenileme anlık görüntüleri denetim için tutulur.

### TTM oranlarının hesaplanması

Finansal tablo kalemlerinin çeyreklik ve YTD tutarları `gold.current_financial_values` görünümündedir. TTM finansal oranları `gold.current_financial_ratios.value_ttm` alanından sorgulanır. Hesaplama, ortak TÜFE bazındaki ardışık dört çeyreğin gelir tablosu tutarlarını toplar. Q4 için raporlanmış tam yıl tutarları doğrudan kullanılabilir. Marjlar toplam kârın toplam hasılata bölünmesiyle hesaplanır. TTM aktif kârlılığı, özkaynak kârlılığı ve aktif devir hızı dönem başı ve sonu bakiyelerinin ortalamasını kullanır. Dönem başı bakiye yoksa sonuç boş kalır. Cari oran, nakit oranı, borç oranı ve borç/özkaynak oranı yalnızca çeyrek sonu için yayımlanır.

EPS ayrı kaynak verilerinden hesaplanır; güncel hisse sayısı veya ödenmiş sermaye geçmiş dönemler için kullanılmaz. Ortak TÜFE bazına dönüşüm, kaynak raporlardaki yeniden düzenleme ve enflasyon muhasebesi farklılıklarını tek başına çözmez.

Mevcut Silver verilerinden Gold finansal değerlerini ve oranlarını yeniden üretmek için `python pipeline.py calculate-gold` kullanın.

### Rapor dönemleri ve ölçüm bazları

Gelir ve nakit akışlarında raporlanan çeyrek/YTD tutarları birlikte korunur. PDF başlığı, kaynak özeti, nominal/enflasyon düzeltmeli/bilinmeyen ölçüm bazı ve satın alma gücü tarihi `silver.financial_report_context` içinde tutulur. Fark alınmadan önce uyumlu enflasyon düzeltmeli tutarlar aynı satın alma gücüne çevrilir; nominal/enflasyon düzeltmeli geçişlerde çeyrek tahmin edilmez. Gold gerçek dönem tarihlerini, `value_real_ytd` tutarını ve hesaplama açıklamalarını saklar. TÜFE dönüşümü için aradaki ayların tamamı gerekir; bu analitik dönüşüm nominal hesapların TMS 29 yeniden düzenlemesini üretmez.

TÜFE ve kur eşleşmesi gerçek rapor bitiş ayını kullanır. USD tutarı ayın açılış referans kuruyla analitik karşılıktır. Büyüme oranları ardışık veya bir önceki yılın aynı dönemlerini kullanır.

`python pipeline.py repair-financials` varsayılan olarak etkin Silver kayıtlarını saklanan Bronze satırlarından ve yerel PDF'lerden onarır. Gold verisi yayımlanmış şirketlerde EPS, kapsama ve F/K'yı da yeniler. Onarılan Silver kayıtlarından Gold finansal değerlerini ve diğer oranları yeniden oluşturmak için `calculate-gold` çalıştırın.

### Hisse başına kazanç (EPS)

EPS, `TRY_per_1_TRY_nominal` birimine dönüştürülür: 1 TL nominal pay tutarı başına TL. Kaynaktaki kuruş/TL, nominal pay değeri, bin/milyon para birimi ve hisse sayısı çarpanları ayrı saklanır. Örneğin 0,01 TL nominal pay için kuruş cinsinden açıklanan değer, TL cinsinden ve 1 TL nominal pay bazındaki değerle doğrudan aynı birim değildir.

`gold.financial_ratios` temel EPS için `eps`, aynı değerin ortak TÜFE hedef ayındaki karşılığı için `eps_inflation_adj` kodunu yayımlar. Toplam temel EPS için `value_quarter`, `value_ytd` ve `value_ttm` alanlarının her biri önce doğrulanmış değeri, yoksa `gold.eps_coverage` içindeki sermaye bazlı tahmini kullanır. Doğrulanmış değer, raporda açıklanan EPS veya açıklanmış kazanç ve ağırlıklı pay sayısından hesaplanan EPS olabilir. Sulandırılmış ve sürdürülen faaliyet EPS türleri yalnızca ayrıntılı `gold.current_eps_values` görünümünde saklanır; toplam temel EPS yerine kullanılmaz. Seçilen değerin kalite bilgisi ve tahmin varsayımları `gold.current_eps_coverage` içindedir. Hiçbir desteklenen değer yoksa alan boş kalır, sıfırla doldurulmaz. 016 geçişi eski oran satırlarını yedekleyerek tek EPS koduna dönüştürür; normal güncellemeler bu yapıyı korur.

`eps_inflation_adj`, seçilen EPS tutarını kendi satın alma gücü tarihinden finansal tabloların ortak TÜFE hedef ayına taşır. Nominal raporlarda gerçek mali dönem sonu analitik baz olarak kullanılır. Baz tarihi, hedef ay veya gerekli TÜFE verileri eksikse sonuç boş kalır. Tarihler, TÜFE çarpanları ve doğrulanmış/tahmini kalite bilgisi `gold.current_eps_coverage.inflation_adjustment` alanındadır.

Çeyreklik EPS, ortak pay ve satın alma gücü bazındaki YTD kazanç ve gün ağırlıklı pay sayılarından türetilebilir. TTM EPS dört ardışık çeyreğin kazancını on iki aylık gün ağırlıklı pay sayısına böler. Q4 için doğrulanmış tam yıl EPS'si doğrudan kullanılabilir. Çeyrekler eksikse, uyumlu açıklamalardaki önceki tam yıl kazancından önceki yılın aynı YTD kazancı çıkarılıp güncel YTD kazancı eklenebilir. Pay sayısı için aynı işlem, her açıklamanın ağırlıklı ortalama pay sayısı gün sayısıyla çarpılarak yapılır. EPS değişim oranları için de doğrulanmış ortak baz gerekir.

Tahminler, ana ortaklık pay sahiplerine ait kazancı tarihsel, gün ağırlıklı çıkarılmış nominal sermayeye böler. Fiyat kaynağındaki sermaye geçmişi yetersizse, uygun dönem sonu bilançosundaki ödenmiş sermaye ayrı bir tahmin yöntemi olarak kullanılabilir. `gold.current_eps_coverage` içindeki `quarter_quality`, `ytd_quality` ve `ttm_quality` alanları `verified`, `estimated` veya `review_required` değerini alır. İnceleme gerektiren dönemler `gold.eps_review_queue` görünümündedir.

Etkin gelir tablosu kayıtlarıyla ilişkili yerel KAP HTML/PDF dosyalarından EPS'yi alın veya saklanan girdilerle yeniden hesaplayın.

```bash
python pipeline.py update-eps --symbols THYAO TUPRS BIMAS
python pipeline.py calculate-eps --symbols THYAO TUPRS BIMAS
```

Şirket verilmezse Gold verisi yayımlanmış şirketler seçilir. `run` ve `update-database` de EPS kaynaklarını tarar. Tanınmayan PDF düzenleri ve birimler `ops.data_quality_results` içinde bildirilir. Metin çıkarımı için Poppler `pdftotext` veya `pypdf` kullanılır. `update-eps --ocr`, `pdftoppm` ve `tesseract` ile taranmış sayfaları işler. `calculate-eps` saklanan girdilerle EPS, kapsama ve F/K'yı yeniler. Önceki türetilmiş satırlar `ops.eps_refresh_snapshots` içinde saklanır.

Doğrulanmış tarihsel girdileri `python pipeline.py import-eps --file verified-eps.json` ile aktarabilirsiniz. [JSON örneği](#verified-eps-json)

### Dönem sonu fiyat/kazanç oranı (F/K)

F/K, gerçek mali dönem sonundaki veya en fazla on takvim günü öncesindeki en güncel İş Yatırım işlem kapanışını kullanır. Özgün kapanış `HG_KAPANIS`, Bronze fiyat kaydında tutulur. Doğrulanmış F/K, bu fiyatı pozitif temel TTM EPS'ye böler ve `gold.current_financial_ratios` görünümünde `price_earnings` koduyla `value_ttm` alanında yayımlanır.

Doğrulanmış TTM EPS eksikse, gerekli fiyat ve kazanç bazları uyumlu olduğunda sermaye bazlı tahmin kullanılabilir. `gold.current_price_earnings` içindeki `best_available_pe_ratio` ve `pe_quality` alanları sonucu ve kalitesini birlikte gösterir. Kalite `verified`, `estimated` veya `unavailable` olabilir. Sıfır veya negatif doğrulanmış EPS için F/K boş kalır. Oran geriye dönüktür. Dönemin EPS'si, kapanış tarihinden sonra açıklanmış olabilir.

## Proje yapısı

```text
database/migrations/    PostgreSQL metadata ve medallion şemaları
data_mappings/          KAP tablo eşlemeleri ve başlangıç şirket metaverisi
models/                 Şirket ve finansal tablo veri yapıları
read_statements/        Rapor arşivlerini bulma, okuma ve seçme
services/               PostgreSQL, indirme ve referans verisi servisleri
tests/                  Otomatik kontroller
pipeline.py             Komut satırı giriş noktası
```

Veri kapsamını, tekrar eden kayıtları ve KAP karşılaştırmalarını [doğrulama defterinden](financial_statement_validation.ipynb) inceleyebilirsiniz.

## Geliştirici notu

Testleri çalıştırmak için proje klasöründe `python -m pytest` komutunu kullanın. Kod biçimi ve yaygın hatalar için `ruff check .` kullanılabilir.

PostgreSQL entegrasyon testleri, `BIST_TEST_POSTGRES_DSN` ile belirtilen sunucuda geçici veritabanları oluşturup kaldırır. Bu bağlantı için `CREATEDB` yetkisi gerekir. Değişken tanımlanmazsa entegrasyon testleri atlanır.

---

<a id="english"></a>

# BIST Financial Data Pipeline

[Türkçe](#turkce) | **English**

## At a glance

This project turns financial statements published by BIST companies on KAP into an organized dataset that is easier to compare across reporting periods. Instead of searching through separate reports one by one, you get a consistent starting point for exploring a company’s financial position and how it changes over time.

The project processes income statements, balance sheets, cash flow statements, and statements of changes in equity. It calculates CPI-adjusted values, USD equivalents, EPS, and financial ratios, including trailing-twelve-month measures. You can explore the results in PostgreSQL, a Jupyter notebook, or a compatible reporting tool.

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
- Query USD equivalents, trailing-twelve-month (TTM) ratios, earnings per share (EPS), and fiscal quarter-end price/earnings (P/E).
- Use the prepared data for company comparisons, charts, and your own research.
- Connect PostgreSQL to a Jupyter notebook or compatible business-intelligence tool.

## Outputs

The main output is the `bist_finance` PostgreSQL database. Use the Gold `current_*` views for reporting and the Bronze, Silver, and Ops records to trace sources and calculations.

| Table or view | Contents |
| --- | --- |
| `metadata.companies` and `metadata.instruments` | Company and traded-share identifiers |
| `bronze.statement_filings` and `bronze.statement_rows` | Raw KAP filing records |
| `silver.current_financial_observations` and `silver.financial_report_context` | Active statement observations, fiscal periods, and measurement bases |
| `silver.monthly_macro_data` and `silver.daily_prices` | Monthly CPI and USD/TRY, daily prices, and historical capital |
| `silver.current_eps_observations` | Active EPS disclosures and period-specific weighted-average share inputs |
| `gold.current_financial_values` | Quarterly, fiscal year-to-date (YTD), CPI-adjusted, and USD values |
| `gold.current_financial_ratios` | Quarterly, YTD, and TTM ratios, including `eps`, `eps_inflation_adj`, and verified P/E |
| `gold.current_price_series` | Published daily closes and CPI-adjusted equivalents |
| `gold.current_eps_values` and `gold.current_eps_coverage` | Detailed verified EPS, capital-based estimates, and per-field quality |
| `gold.current_price_earnings` | Verified and estimated quarter-end P/E, price and EPS provenance, and quality |
| `ops.pipeline_runs` and `ops.data_quality_results` | Run status and data-quality records |

Statement rows include the company, year, quarter, item name, and, when recognized, the item’s parent headings. This helps distinguish same-named items that appear in different sections. Nominal amounts, inflation-adjusted amounts, and USD equivalents are stored separately.

## Quick start

You need Python 3.11+ and a running PostgreSQL 15+ server.

### Windows

Open PowerShell in the project folder and run:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell prevents activation, use `.\.venv\Scripts\python.exe` in place of `python`.

### macOS and Linux

Open Terminal in the project folder and run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### Set up PostgreSQL

Set the connection in your shell, replacing `YOUR_USER` with your PostgreSQL role. On macOS or Linux, use the following command.

```bash
export BIST_FINANCE_DSN="dbname=bist_finance host=localhost user=YOUR_USER"
```

In PowerShell, use the following command.

```powershell
$env:BIST_FINANCE_DSN = "dbname=bist_finance host=localhost user=YOUR_USER"
```

The connection uses `BIST_FINANCE_DSN` first, then `DATABASE_URL`, then the local default `dbname=bist_finance`. The connected database must be named `bist_finance`.

```sh
python pipeline.py init-postgres --create-database
```

This creates `bist_finance` if needed and applies the [checked-in SQL migrations](database/migrations/) for the metadata, Bronze, Silver, Gold, and Ops schemas. It loads the bundled JSON records when the metadata tables are empty. Rerunning setup preserves existing company metadata.

`--create-database` requires access to the `postgres` database and `CREATEDB` privileges if `bist_finance` is missing. If your database administrator has already created `bist_finance`, run `python pipeline.py init-postgres` with a role that can create schemas and tables there.

On an existing installation, run `migrate-postgres` to update the schema, then `calculate-eps` to populate CPI-adjusted EPS and estimated P/E for published periods.

### Run the financial pipeline

Download and process THYAO, TUPRS, and BIMAS from 2017 Q1 through 2026 Q2.

```bash
python pipeline.py run --symbols THYAO TUPRS BIMAS --begin-year 2017 --begin-quarter 1 --end-year 2026 --end-quarter 2
```

The command refreshes reference data, stores annual KAP archives under `financial_statements/<SYMBOL>/<SYMBOL>_<YEAR>.zip`, and publishes the selected periods. The inflation target defaults to the latest stored CPI month. Fetch daily prices separately with `update-prices`, then refresh P/E with `calculate-pe`.

## Run individual steps

You can also run each step separately:

```bash
# Download KAP archives only
python pipeline.py download --symbols THYAO TUPRS BIMAS --begin-year 2017 --end-year 2026

# Refresh inflation and USD/TRY reference tables
python pipeline.py update-reference-data

# Process archives that are already stored locally
python pipeline.py update-database --symbols THYAO TUPRS BIMAS --begin-year 2017 --begin-quarter 1 --end-year 2026 --end-quarter 2

# Rebuild Gold financial data from existing Silver observations
python pipeline.py calculate-gold --symbols THYAO TUPRS BIMAS

# Download daily prices
python pipeline.py update-prices --symbols THYAO TUPRS BIMAS --begin-date 2017-01-01 --end-date 2026-06-30

# Refresh fiscal quarter-end P/E using stored prices
python pipeline.py calculate-pe --symbols THYAO TUPRS BIMAS

# Recalculate stored prices to a selected inflation level without downloading
python pipeline.py adjust-prices --symbols THYAO TUPRS BIMAS --price-level-year 2026 --price-level-month 6

# Refresh existing company metadata from KAP
python -m services.update_company_info --symbols THYAO TUPRS BIMAS
```

Use `--skip-download` or `--skip-reference-data` with `run` to reuse existing inputs. Omitting `--symbols` selects every company in PostgreSQL metadata for `run`, `download`, `update-database`, and `update-prices`. By default, `calculate-gold` uses all active Silver filings, `calculate-pe` uses all published Gold EPS records eligible for valuation, and `adjust-prices` uses all stored prices. Run `python pipeline.py <command> --help` to see the available options.

Set both `--price-level-year` and `--price-level-month` to choose an explicit CPI target for `run`, `update-database`, or `calculate-gold`. Passing both flags to `update-prices` also publishes the Gold price series. `adjust-prices` requires both flags.

## Data protection and behavior

- Statement ingestion, EPS ingestion, and Gold publication commit separately. A failed Gold calculation leaves the prior Gold publication active.
- Missing local reports leave the corresponding Silver records active. If no matching archive exists, statement ingestion stops before writing statement data. A `run` may already have refreshed reference data at that point.
- `run`, `download`, and `update-database` reject reversed periods before work begins.
- Downloaded KAP archives stay on disk for future reprocessing.

## Data sources and limitations

- Financial statements come from company reports published on KAP.
- Inflation data comes from the Central Bank of the Republic of Türkiye (TCMB) consumer price data.
- USD/TRY reference data comes from the Frankfurter service and is summarized into monthly values.
- Daily prices and historical capital come from İş Yatırım. Each symbol is committed separately, and re-running updates the rows returned by the source.
- Some KAP files show year-to-date cumulative figures rather than standalone quarterly values. The project converts applicable income statement values into standalone quarters.
- KAP report layouts can change, and automatic parsing may not recognize every item perfectly. The database supports analysis; it does not replace the official reports.
- Inflation and exchange-rate adjustments are limited by the source frequency and monthly reference method. They do not represent a company’s actual foreign-currency transactions or the inflation applicable on each item’s transaction date.

### Cumulative cash flows and repairing older records

Six- and nine-month cash flows retain their actual fiscal quarter, period dates, and `is_cumulative` flag in Silver. Gold derives standalone flows by subtracting the preceding cumulative period. If the required prior period is missing, the quarterly amount remains null while the reported YTD amount is retained. Opening and closing cash balances remain balances rather than being differenced or summed. When both reports use inflation-adjusted units, prior YTD is converted to the current report’s purchasing power before subtraction. Unknown bases and nominal-to-inflation-adjusted transitions do not produce derived quarters.

To repair records that the older version incorrectly labeled Q4, rebuild cash-flow observations from retained Bronze data without downloading reports:

```bash
python pipeline.py repair-cash-flows
```

Add `--symbols THYAO TUPRS BIMAS` to limit the scope. The command republishes only cash-flow Silver observations and retains original Bronze filings and previous Silver versions. It recalculates already-published Gold companies using their existing inflation targets, without publishing companies whose Gold data was not previously active.

## PostgreSQL financial pipeline

Gold calculations are published with a run ID. Successful recalculations prune superseded financial-value and ratio rows for the same periods and price-series rows for the same trading dates. Bronze sources, Silver filing history, P/E valuations, and EPS refresh snapshots support auditing.

`run` and `update-database` parse local statements with two worker processes by default. Set `--parse-workers 4` to increase this or `--parse-workers 1` for serial parsing. All parsed frames are retained in memory. Database writes stay in the main process, and KAP request pacing is configured separately. EPS extraction has its own workers (default: two).

### Quarter-end price/earnings

`calculate-pe` refreshes P/E from published Gold basic total TTM EPS and stored Is Yatirim original prices, without downloading or rebuilding financial statements. Use `--symbols THYAO TUPRS` to limit companies. Normal Gold calculations also refresh P/E automatically.

```bash
python pipeline.py calculate-pe
```

The price is the last available trading close on or before the **actual fiscal period end**, within ten calendar days. It uses original `HG_KAPANIS` (`at_the_date_close_price`) retained in Bronze, rather than the corporate-action-adjusted `HGDG_KAPANIS` stored as `silver.daily_prices.close`. P/E is original TRY close divided by verified basic total TTM EPS in TRY per 1 TRY nominal capital. Quarterly/YTD EPS is never annualized, and P/E is null for nonpositive earnings, missing original prices, unsupported split or purchasing-power bases, or ambiguous multiple share classes. Missing inputs are never treated as zero.

`gold.current_financial_ratios` exposes `ratio_code='price_earnings'` in `value_ttm`; `value_quarter` and `value_ytd` are null. `gold.current_price_earnings` exposes price date, original close, EPS, source-price row ID, financial/valuation run IDs, and calculation details including null reasons. The historical ratio is **retrospective**: the quarter's EPS may have been published after the selected price date. It is not a publication-aware backtesting dataset. A new standalone valuation run preserves prior valuation snapshots. Rerun `calculate-pe` after updating prices if financials have not been rebuilt.

For broader coverage, both the valuation table and current view also expose `estimated_eps_ttm`, `estimated_pe_ratio`, `best_available_pe_ratio`, and `pe_quality` (`verified`, `estimated`, or `unavailable`). `pe_ratio` and financial-ratio P/E remain verified-only. Estimates use the existing historical-capital EPS coverage, the same original-price policy, and a complete trailing-year profit period with a known purchasing-power basis. They are used only when verified TTM EPS is absent; a reported loss or zero EPS never falls back to a positive estimate. Estimate evidence, denominator method, CPI factors and assumptions are retained under `calculation_details.estimate`. Keep `pe_quality` visible when using `best_available_pe_ratio`. Use `gold.current_price_earnings` for the latest results; `gold.price_earnings_values` intentionally retains historical runs, including their previous nulls. After changing EPS logic, run `calculate-eps` to refresh EPS, coverage and valuation together.

Valuation checks use `ttm_source_ids`, which identify only the disclosures needed for TTM EPS. Quarterly-only dependencies cannot invalidate a separately reported annual EPS. Derived quarterly and TTM CPI conversions retain their full conversion chain, including the annual/YTD TTM fallback.

### TTM ratio calculations

Statement line items are available at quarterly and YTD grain in `gold.current_financial_values`. Query TTM ratios through `gold.current_financial_ratios.value_ttm`. Trailing inputs use four consecutive quarters in a common CPI base or the reported full-year figures at fiscal Q4. Margins divide trailing profit by trailing revenue. TTM ROA, ROE, and asset turnover use the average of the opening and closing balances. Missing opening balances produce null results. Current, cash, debt, and debt-to-equity ratios are published as quarter-end values.

EPS uses separate source disclosures; current shares and paid-in capital are never used as historical denominators. Normalizing monetary amounts to a common CPI base does not by itself reconcile restatements or differing inflation-accounting bases in the source reports.

Rebuild Gold financial values and ratios from existing Silver data with `python pipeline.py calculate-gold`.

### Reported periods and measurement bases

Both reported quarterly and YTD income/cash-flow amounts are retained in Silver. `silver.financial_report_context` records the PDF presentation header, source hash, nominal/inflation-adjusted/unknown basis, and purchasing-power date. Gold preserves reported YTD instead of adding quarters at different price levels. Derived quarters and YTD totals require compatible scopes and bases; unknown or changed bases produce nulls. Reported full-year YTD can directly supply Q4 trailing ratio inputs even when Q4 itself cannot be derived safely.

Gold records actual quarter dates, basis, `value_real_ytd`, and calculation details. CPI transformations require every intervening month. These transformations are analytical comparisons; they do not reconstruct IAS 29 restatements of nominal accounts. CPI/FX lookup uses the actual reporting end month, including non-calendar years. `value_usd` uses that month’s opening USD/TRY reference rate, matched by year/month regardless of its trading date; it is an analytical equivalent, not an IAS 21 accounting translation. Growth requires the exact preceding quarter or prior-year quarter and compatible calendar spacing. Non-EPS TTM requires four contiguous quarters spanning a full year, or a verified reported full year at Q4.

Use `repair-financials` to restore active Silver observations and report contexts from retained Bronze rows and local PDFs.

```bash
python pipeline.py repair-financials
# Or limit the repair:
python pipeline.py repair-financials --symbols THYAO TUPRS BIMAS
```

The repair also refreshes EPS, coverage, and P/E for companies with published Gold data. Run `calculate-gold` to rebuild financial values and other ratios from the repaired Silver observations. Missing or ambiguous PDF presentation headers are recorded in `ops.data_quality_results`. Regular statement ingestion records the same contexts.

### Earnings per share (EPS)

EPS is normalized to `TRY_per_1_TRY_nominal`: TRY per 1 TRY nominal share capital. Original kuruş/TRY units, nominal share value, monetary multipliers, share-count multipliers, and explicit split factors are preserved separately. A kuruş figure for a 0.01 TRY nominal share must not be treated as TRY EPS for a 1 TRY nominal share without normalization.

`gold.financial_ratios` publishes `eps` for total basic earnings per 1 TRY nominal share capital, plus `eps_inflation_adj` for the same selected EPS in the financial dataset's common CPI target month. Each of `value_quarter`, `value_ytd` and `value_ttm` independently uses verified EPS first and then the capital-based estimate from `gold.eps_coverage`. Verified EPS includes reported values and calculations supported by disclosed earnings and weighted-average shares. The ratio does not substitute diluted or continuing-operation EPS for missing total basic EPS. Only periods with neither a verified value nor a supported estimate remain null; missing values are never replaced with zero. All detailed EPS types and calculation provenance remain in `gold.current_eps_values`, while `gold.current_eps_coverage` exposes the chosen values, per-field quality, estimate methods and assumptions. Migration 016 snapshots existing EPS ratio rows before consolidating the codes; normal `update-database` and `calculate-eps` runs maintain both the original and inflation-adjusted series.

`eps_inflation_adj` independently multiplies each selected quarter/YTD/TTM EPS by target CPI divided by its own source-date CPI. Already inflation-adjusted disclosures start from their disclosed purchasing-power dates, including TTM calculations already reconciled to a common period-end basis; nominal reports use their actual fiscal period end as an analytical convention, matching financial values. This does not recreate IAS 29 accounts or normalize changes in share capital. Target year/month comes from the matching Gold financial values, so EPS follows the same target as the rest of the dashboard. Missing dates, ambiguous targets or incomplete CPI leave the adjusted field null. Dates, factors, verified/estimated quality and unavailability reasons are in `gold.current_eps_coverage.inflation_adjustment`. Original `eps` and P/E are unchanged; EPS growth fields for the adjusted series are currently unpopulated. Migration 017 permits this additional code without restoring diluted/continuing-operation ratio codes.

Quarterly EPS is not obtained by subtracting YTD EPS. It can be derived from YTD attributable earnings and day-weighted shares only with verified common share and purchasing-power bases. TTM EPS divides four consecutive quarters of attributable earnings by the twelve-month day-weighted share count. A verified full-year report can directly supply Q4 TTM EPS. Splits and bonus issues are not guessed; inflation-adjusted profits can be reconciled using complete monthly CPI spans, with every conversion recorded. EPS growth is published only when both values have a verified common basis; growth fields remain null for capital-based estimates. Reported period EPS is preserved; derived CPI-based TTM uses a distinct calculation method in its provenance.

When individual quarters are unavailable, verified TTM can also use prior annual profit minus prior same-period YTD profit plus current YTD profit. Its denominator applies the same arithmetic to each disclosure's weighted-average shares multiplied by its actual calendar days, then divides by trailing-year days. This fallback requires complete disclosed inputs, matching fiscal dates, a positive residual share-day denominator, and verified compatible share and purchasing-power bases; it never subtracts reported EPS. Provenance uses `verified_annual_ytd_bridge` (or `_cpi`) and retains all three source IDs and any CPI conversions. Directly disclosed quarterly earnings/share inputs take precedence over reconstructed inputs, even when rounded EPS agrees. A first-fiscal-quarter disclosure also supplies YTD EPS.

Backfill EPS from local KAP HTML/PDF files associated with active income-statement filings, or recompute it from stored inputs.

```bash
python pipeline.py update-eps --symbols THYAO TUPRS BIMAS
python pipeline.py calculate-eps --symbols THYAO TUPRS BIMAS
```

Omitting symbols selects only companies with published Gold data. Normal `run` and `update-database` processing also scans EPS sources. Unsupported layouts/units are recorded in `ops.data_quality_results`. No reports are downloaded. Text extraction uses Poppler `pdftotext` if available and otherwise `pypdf`.

The EPS reader also extracts KAP HTML `new-type-row` cells, mapping each numeric cell to its own date header while preserving blank positions. PDF notes verify currency/nominal-share units and reconcile disclosed earnings with historical weighted-average shares; the financial statement monetary multiplier is never applied to EPS. Wrapped PDF dates, alternate note headings, and explicit or uniquely reconciled share-count scales are supported. Unresolved units, source disagreements, and unsupported layouts remain unavailable with diagnostic reasons. Automatic ingestion preserves verified JSON inputs and compatible existing observations.

`update-eps --workers 4` refreshes local EPS using four bounded extraction workers, then atomically refreshes EPS, coverage and P/E against existing published financial datasets. Workers do no database writes; the default is two workers. Structured source amounts, date columns, PDF evidence, and normalization details are retained in `bronze.eps_sources.source_payload` and `silver.eps_observations.normalization`.

`calculate-eps` refreshes derived EPS, coverage, and P/E against the published financial dataset using stored observations and prices. The EPS refresh run is recorded as `calculation_details.eps_refresh_run_id`. `ops.eps_refresh_snapshots` retains replaced EPS, coverage, and ratio rows. A failed refresh rolls back its derived changes and snapshots. Full Gold calculations also calculate EPS coverage automatically.

The recovery reader accepts explicitly declared EPS currency/nominal units independently of calculation-note extraction, preserves blank cells in mixed-currency PDF tables, and calculates basic EPS from independently disclosed parent-attributable earnings and weighted-average shares even when only diluted EPS is printed. It supports Roman-numbered note headings. Optional `update-eps --ocr` processes image-only pages locally using `pdftoppm` and `tesseract` (install Turkish language data for best results); OCR numbers still pass the same date, unit and reconciliation checks. Unknown units or actual source contradictions are never filled by assumption.

EPS-note reporting units take precedence over legal-share nominal values in capital notes. For example a 0.01 TRY legal share can coexist with EPS stated per 1 TRY nominal capital; applying the legal-share conversion to that EPS produces a 100-fold error. Direct reported-value recovery therefore requires an EPS-local nominal declaration, including attached note footnotes, and keeps neighboring basic/diluted currency labels separate.

Gold also builds `gold.current_eps_coverage`, one row per company/fiscal period. Its `verified_value_quarter/ytd/ttm` fields remain source-verified; `estimated_value_quarter/ytd/ttm` use parent-attributable earnings divided by calendar-day-weighted historical **issued nominal capital** from İş Yatırım. The individual legal-share nominal value cancels in our TRY-per-1-TRY-nominal output unit. Estimates do not deduct unknown treasury shares, infer preference rights, or retrospectively classify bonus/rights issues; these assumptions, capital-history hashes, calculation evidence, and missing-input reasons are retained in `assessment`. Estimates can carry the first observed historical capital back by up to seven opening calendar days (for example January 1 before the first stored trading day); such cases have an explicit `opening_capital_assumption` in the evidence and never establish a verified share basis. Multiple instruments prevent estimation; later capital gaps over ten days prevent price-based weighting and can trigger the explicit reported-capital proxy below. No current-company-capital substitution is made.

If price-feed capital history is unavailable or stale, a uniquely reported positive `ödenmiş sermaye` value from the same period's balance sheet can supply a **period-end paid-capital proxy**, explicitly identified by `assessment.evidence.<period>.denominator_method='period_end_reported_paid_in_capital_proxy'`. This does not establish a weighted-average denominator, treasury adjustment, or verified nominal/legal interpretation of an inflation-accounting balance. Known multiple instruments still prevent this proxy. For older financial-company templates, standalone net profit is usable only when the linked source filing explicitly says `Konsolide Olmayan`; consolidated bank group profit additionally reconciles to net profit less minority interests. These numerator rules do not substitute comprehensive income.

An explicitly zero income-statement minority-profit component can also establish that total net profit equals parent profit. Missing minority rows are never treated as zero; contradictory minority rows block this inference. A bank group-profit row can use this rule when the net-profit row is absent, but an available contradictory net/group/minority reconciliation is never bypassed.

When an older consolidated template omits parent profit, the coverage estimate can reconstruct it as net profit minus an explicitly disclosed minority-profit component. Both inputs must be unique, have matching periods and measurement bases, and share a source filing; an existing contradictory explicit parent/group row prevents this reconstruction. Both input financial-value IDs are recorded in the estimate evidence.

For Power BI, `best_available_quarter/ytd/ttm` prefer verified values over estimates, and the matching `quarter_quality/ytd_quality/ttm_quality` columns explicitly say `verified`, `estimated`, or `review_required`. Keep these quality fields visible when using best-available values. `gold.eps_review_queue` lists periods requiring estimates or further review. The single `eps` ratio uses these best-available values; verified `gold.eps_values` and verified P/E fields remain free of estimates. Negative EPS remains available even though P/E is suppressed for nonpositive earnings.

Disclosed historical outstanding-share denominators can acquire a common share basis when dated İş Yatırım capital confirms a stable issued-capital regime across their full periods and the counts reconcile to issued capital. Capital changes require independently verified corporate-action treatment (or the existing verified JSON import); no arbitrary common basis identifier is assigned. Verified four-quarter TTM calculations convert inflation-adjusted profits to the final period's purchasing power using complete monthly CPI spans before dividing by the day-weighted denominator. Nominal/inflation-adjusted transitions remain unsupported. Early history may require preceding-year reports and opening capital; source absence is recorded rather than guessed.

<a id="verified-eps-json"></a>

#### Import verified EPS inputs

Import verified disclosures with `python pipeline.py import-eps --file verified-eps.json`. Replace the placeholder symbol and sample values below with data from the source filing.

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
data_mappings/          KAP statement mappings and seed company metadata
models/                 Company and financial-statement data structures
read_statements/        Finding, reading, and selecting report archives
services/               PostgreSQL, download, and reference-data services
tests/                  Automated checks
pipeline.py             Command-line entry point
```

Use the [validation notebook](financial_statement_validation.ipynb) to check published coverage, duplicate candidates, and selected values against KAP filings.

## For contributors

Run the tests from the project folder with `python -m pytest`. Run `ruff check .` for code-quality checks.

PostgreSQL integration tests use a disposable database and require a role with `CREATEDB`. Set `BIST_TEST_POSTGRES_DSN` to the connection details for that server, then run `python -m pytest`. Without that variable, database integration tests are skipped. The supplied database is never migrated or recalculated by these tests.
