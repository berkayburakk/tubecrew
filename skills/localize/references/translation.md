# Translation brief

Give this page to whoever translates (an agent per language works well; translations can run in parallel).

## Inputs

- The final `script.md` (after the fact-check), `facts.json`, and `upload.json` (title, description, chapters).
- What is on screen and when: numbers and names shown on cards. Keep the order of numbers and names inside each
  paragraph, so each one is heard while its card is visible.
- The channel's notes on tone and the regional variant chosen for each language, if they exist.

## Two texts for every sentence

| Text | Used for | Numbers |
|---|---|---|
| `dub/<lang>/script.md` | captions, the voice_check comparison | digits, in local format (decimal comma or point, currency position) |
| `dub/<lang>/tts.md` | the voice engine | no digits: numbers, percentages, money and years written as words |

- Abbreviations go into the TTS text the way they are said in that language; product names stay as written.
- Some engines read Japanese kanji with Chinese readings. For such an engine, write the TTS text in kana and write
  a long number as one unbroken kana run (spaces inside a number can change the language the engine reads it in).
- Check every number twice: the caption digits and the TTS words must say the same value.

## Large numbers in Chinese, Japanese and Korean

These languages count in units of 10^4: 万 / 만 = 10^4, 亿 / 億 / 억 = 10^8, 兆 / 조 = 10^12 (Chinese writes 万亿).
Compute every conversion twice. Made-up examples:

| Source | zh | ja | ko |
|---|---|---|---|
| Acme's $12.5 billion | 125亿美元 | 125億ドル | 125억 달러 |
| about 40 percent | 约40% | 約40% | 약 40% |
| more than $1.2 trillion | 超过1.2万亿美元 | 1.2兆ドル超 | 1.2조 달러 이상 |

Check: 1 billion = 10亿 = 10億 = 10억.

## Length targets

The dub has to fit the picture. A useful per-paragraph target is the TTS text's character count divided by the
source paragraph's character count. These upper limits kept dubs inside their slots for a tightly paced English
narration (about 0.3 s between paragraphs); a slower source leaves more room. `dub_fit.py` has the final word.

| Language | Target ratio (at most) |
|---|---|
| es, it, pt, ru, tr | 0.90 |
| fr, de | 0.92 |
| ko | 0.42 |
| ja | 0.40 |
| zh | 0.28 |

To shorten, drop filler, repetition and rhetorical decoration. Never drop a number, a company or source name, a
direction (up or down, profit or loss) or a hedge.

## Regional variant and form of address

Starting points only; the creator decides. Record the choice in the channel's notes and keep it for every video.

| Language | Variant | Address | Translationese to avoid |
|---|---|---|---|
| de | Germany | du is common in German explainer videos | stiff nominal style ("fand ... statt" for a trade) |
| es | neutral Latin American | tú, ustedes (no vosotros) | Spain-only words in a Latin American dub |
| fr | France | vous | English word order |
| it | Italy | tu | bureaucratic verbs ("effettuare") |
| pt | Brazil | você | European Portuguese forms in a Brazilian dub |
| ru | Russia | вы | English sentence order |
| ja | standard, です/ます | (none) | long katakana chains |
| ko | 해요체 | (none) | literal passive (-에 의해) |
| zh | Mandarin, Simplified | 你 | written news register |

## Meaning rules

- Keep distinctions exactly: profit vs revenue, gross vs net, reported vs expected, a scenario vs an event, a
  partner vs a customer, a deposit vs income.
- Hedges and sources survive in every language: "about", "nearly", "estimates", "expects", "reportedly", the
  source's name.
- No advice, no price targets, no stronger tone than the source.
- A mention of another video stays a mention: translate its title, keep its URL.

## Title, description and captions

- Title at most 100 characters, the same promise as the source title, natural in the target language.
- Description: a full translation; chapter timestamps unchanged (the picture is the same), URLs unchanged,
  disclaimers kept; at most 5000 bytes and no `<` or `>` (YouTube rejects them).
- Captions: at most 2 lines of 42 characters (22 for Chinese, Japanese and Korean), a number on the same line as
  its unit, cues in order without overlaps, the last cue inside the video. Do not break a Latin word or a number
  inside Chinese or Japanese text. Until the dub exists, translated captions on the original timing can be
  uploaded for the original audio.

## Native-listener audit report

Save as `dub/<lang>/AUDIT.md`:

```
# <lang> native-listener audit
Audit by: model (not a native speaker) | native speaker
Verdict: GO | GO after fixes | NO-GO

## Fixes
| Paragraph | Before | After | Why (meaning, hedge, time direction, subject, translationese, grammar) |

## Correct but could be better (not changed)
```

Made-up example, glossed in English: P10, hedge: "Acme keeps the deposit" became "Acme can keep the deposit",
because the source says "can keep". Description, time direction: "booked chips years ago" became "booked chips
years ahead".
