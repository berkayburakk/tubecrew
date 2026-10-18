# Kanal kuralları

Claude Code bu dosyayı her oturumda okur. Kendi kanalınıza göre düzenleyin.

## Doğruluk
- Her rakamın kaynağı `rakamlar.json`'da yazılı olsun. Linki olmayan rakam kullanılmaz.
- Uydurma kanıt, uydurma alıntı ya da uydurma kaynak yok.
- Tavsiye vermek yok: anlat, yönlendirme.

## Üretim
- Her adımın çıktısı bir dosyaya yazılır; kontrolden geçmeyen adımdan sonrakine geçilmez.
- Metni yazan model kendi metnini denetlemez; denetimi işi yapmamış bir ajan yapar.
- Yüklemeden önce işi yapmamış bir ajan videoyu baştan izler.
- Video önce gizli yüklenir; herkese açma kararı insanındır.

## Dersler
Her hatadan sonra buraya bir satır ekleyin. Örnek:
- Ekranda vurgulanan rakam, o an söylenen rakamdan farklıydı. Render denetiminde rakam ile söz eşleşmesi kontrol edilir.
