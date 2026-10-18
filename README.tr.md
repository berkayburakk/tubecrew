# tubecrew

**Kodlama ajanın için bir YouTube yapım ekibi.** tubecrew, Claude Code'u (ya da Codex'i) bir video üretim hattına
çevirir. Konuyu kanıta bakarak seçer ve her rakamın kaynağını tutar. Metni insan gibi konuşacak şekilde yazar.
Sesi ve render'ı kodla denetler, işi yapmayan ajanlara inceletir, videoyu gizli olarak yükler. Yayından sonra da
izleyicinin hangi sahnede ayrıldığını söyler.

*English: [README.md](README.md)*

## Nasıl çalışır

Video duraklardan geçer. Her durak bir dosya yazar; bir sonraki durak ancak kapı geçilince başlar.

| Durak | Skill / ajan | Kapı |
|---|---|---|
| Konu | `find-topic`, ardından `topic-skeptic` | en az bir aday kanıtıyla AYAKTA |
| Araştırma | `research-facts` | `facts_check.py` 0 ile çıkar |
| Metin | `write-script`, ardından `script-auditor` | lint geçer, A/B kararı işlenir |
| Rakamlar | `facts_check.py --script`, ardından `fact-checker` | kaynaksız rakam 0, PASS |
| İlk 60-90 sn | sen + içerik üreticisi | açılış onaylandı |
| Ses | kendi TTS'in ya da kendi sesin, ardından `check-voiceover` | `voice_check.py` 0 ile çıkar |
| Render | `check-render`, ardından `render-auditor` | aynı dosya için iki PASS |
| Yükleme | `upload-checker`, ardından `upload-private` | GO dosyası var, video **gizli** yüklendi |
| Yayın | içerik üreticisi, YouTube Studio'da | yayınla tuşuna bir insan bastı |
| Öğrenme | 3+ gün sonra `retention-by-scene` | bir ders yazıldı |

İş üçe ayrılır:

- **Düşünme** (hangi konu, hangi hikâye, ne güzel görünür) modelde ve içerik üreticisinde kalır.
- **Tekrar eden iş** (kelime saymak, ses seviyesini ölçmek, rakamı kaynakla eşleştirmek, metadata sınırlarını
  denetlemek) çıkış kodu olan küçük betiklerdedir. Betik 1 ile çıkarsa "işi düzelt" demektir.
- **Denetim**, işi yapmamış bir ajana verilir.

## Farkı ne

- **Kural değil kapı.** Aynı hata iki kez olunca daha uzun bir talimat yazılmaz, betiğe bir kontrol eklenir.
  Skill'ler kısa kalır.
- **Karar dosyaya bağlıdır.** Render onayı yalnız incelediği dosya için geçerlidir (SHA-256). Video yeniden
  render edilirse yükleme betiği eski onayı kabul etmez.
- **Her rakamın kaynağı var.** Metinde yalnız `facts.json`'da linkiyle duran rakamlar olabilir. Metin kapısı
  olmayanları bulur.
- **Hiçbir şey kendiliğinden yayınlanmaz.** Yükleme her zaman gizlidir. Kendi OAuth istemcin kullanılır, anahtarlar
  proje dışında durur.
- **Yayından sonra ölçer.** İzlenme eğrisi doğrudan YouTube Analytics API'den gelir ve bölümlerin ya da metnin
  üstüne oturtulur.
- **Bağlamı şişirmez.** Oturum kancası yok, açılışta hiçbir şey enjekte edilmez. Dokuz skill ve altı ajan bir
  oturuma toplam ~1.800 token ekler (`claude plugin details tubecrew`). Skill'in gövdesi yalnız kullanılınca
  yüklenir, çoğu 1.500 tokenın altındadır.

## Kurulum

**Claude Code:**

```
/plugin marketplace add berkayburakk/tubecrew
/plugin install tubecrew@tubecrew
```

**Codex:** `skills/*` klasörlerini `~/.codex/skills/` (ya da deponun `.agents/skills/` klasörü) içine,
`codex/agents/*.toml` dosyalarını `~/.codex/agents/` içine kopyala.

**Gerekenler:** Python 3.9+, `ffmpeg`/`ffprobe`, konu taraması için `yt-dlp`. Yükleme ve izlenme ölçümü için
`pip install google-auth google-auth-oauthlib requests` ve kendi Google Cloud OAuth istemcin
([kurulum rehberi](skills/upload-private/references/oauth-setup.md)). Ses dökümü için isteğe bağlı
`faster-whisper`. Hareketli grafik için Node 18+.

## İçinde ne var

**Skill'ler:** `produce-video` (sıra ve kapılar), `find-topic`, `research-facts`, `write-script`,
`check-voiceover`, `check-render`, `upload-private`, `retention-by-scene`, `morph-clips` (söylenen kelimeye oturan,
hiç kesilmeden şekil değiştiren hareketli grafik).

**Denetçi ajanlar:** `topic-skeptic`, `fact-checker`, `script-auditor`, `render-auditor`, `upload-checker`,
`privacy-scanner`. İşi yapan kendi işini denetlemez. Denetim yayına gidecek iş içindir; taslaklar atlanır.

## Asla yapmadıkları

- Videoyu herkese açmak, zamanlamak ya da bir şey silmek.
- Render kontrolü, render incelemesi ve yükleme incelemesi aynı dosyayı onaylamadan yüklemek.
- Anahtar ya da token'ı projeye yazmak.
- Kendi videolarını izlemek, beğenmek ya da yorum yazmak. Otomatik etkileşim YouTube kurallarına aykırıdır ve
  bütün kanalların bağlı olduğu para kazanma hesabını riske atar.
- Rakam, alıntı ya da sonuç uydurmak.

## Başlangıç seti

Daha sade bir başlangıç istersen [`examples/starter-kit`](examples/starter-kit): bir projeye bir dakikada
kopyalanan tek skill'lik sürüm (Türkçe ve İngilizce).

## Lisans

MIT. Ayrıntı [LICENSE](LICENSE), üçüncü taraf notları [NOTICE.md](NOTICE.md).
