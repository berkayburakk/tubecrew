---
name: video-uret
description: Bir YouTube videosunu konu seçiminden gizli yüklemeye kadar adım adım üretir. "Video üret", "yeni video" ya da "video hazırla" dendiğinde kullan.
---

# Video üretim akışı (sade hal)

İlke: her adımın çıktısı bir dosyaya yazılır. Bir adım bitip kontrolden geçmeden sonrakine geçme.
Karar gereken yerde dur ve bana sor. Proje klasörü: `projeler/<video-adi>/`

## 1. Konu
- 10-15 konu adayı çıkar: gündemi, rakip kanallarda tutan videoları ve aramadaki ilgiyi tara.
- Her adayın yanına sayı yaz: o konudaki en iyi rakip video kendi kanal ortalamasının kaç katı izlenmiş,
  ilgi artıyor mu, aynı konuyu kaç kanal yapmış.
- Bir şüpheci gibi adayları sına, en güçlü beşi sırala. Seçimi ben yaparım. → `konu.md`

## 2. Araştırma
- Her rakamı `rakamlar.json`'a yaz: değer, birim, tür (resmi / haber / kendi hesabımız), link, erişim tarihi.
- Linki olmayan rakam kullanılmaz. Bulduğun ama güvenmediğin bilgiyi `kullanilmasin.md`'ye yaz.
- Rakamları mümkünse ikinci bir modele bağımsız doğrulat.

## 3. Senaryo
- İki farklı açılış yaz; ben seçerim.
- Her paragraf bir sahne. Ekranda bir şey olması gereken kelimenin önüne işaret koy: `[[isaret]]kelime`.
- Rakamları elle yazma; `rakamlar.json`'dan al. → `senaryo.md`

## 4. Bağımsız denetim
- Senaryoyu yazmamış bir ajana ver: yanlış ve yanıltıcı ifadeleri kaynaklarla karşılaştırıp listelesin.
- Yanlış ve yanıltıcı sayısı sıfır olmadan sese geçme. → `denetim.md`

## 5. Ses
- Paragraf paragraf üret, her kelimenin zamanını sakla. Ses ayarı kodda sabit kalsın.
- Bir paragraf değişirse yalnız o paragraf yeniden üretilir.
- Üretilen sesi Whisper gibi bir araçla geri yazdır; yanlış okunan kelimeyi değiştirip paragrafı yeniden üret.

## 6. Görsel
- Bütün karelere aynı stil referansını ver.
- Hareketli klip gerekiyorsa önce kendi bilgisayarında çalışan açık bir model dene;
  kredili bulut video modelini yalnız ben istersem kullan.

## 7. Kurgu ve ses tasarımı
- Kurguyu kodla kur. Ekrandaki her olay, işaretli kelimenin söylendiği ana bağlı olsun.
- Ekrandaki rakam o an söylenen rakamla aynı olsun.
- Müzik konuşmanın altında kısılsın; son ses düzeyi YouTube için yaklaşık −14 LUFS olsun.

## 8. Kapak ve render denetimi
- Birkaç kapak üret; yazıyı ve logoları tek tek kontrol et.
- Videoyu yapmamış bir ajan render'ı baştan izlesin: ekrandaki rakam söylenenle aynı mı,
  yazı taşıyor mu, ses kopuyor mu. → `render-denetimi.md`

## 9. Yayın
- Önce gizli (private) yükle; başlık, açıklama ve bölümler hazır olsun.
- Son kontrolü ben yaparım; herkese açma kararı benim.

## Kurallar
- `CLAUDE.md`'deki kurallar her adımda geçerli.
- Bir hata yaşandığında `CLAUDE.md`'ye ders olarak bir satır ekle; bir sonraki video aynı hatayı yapmasın.
