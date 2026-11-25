# 構造を残して HTML を読む(試験的)

このページは、試験用の branch `exp/html-structure` の説明です。release には含まれず、
内容は変わったり、なくなったりすることがあります。

表の 1 行に書かれているのは値です。それが何の値で、何に属するかは、上の見出し行と
横の見出しセルに書かれていて、chunk がそれを持っているとは限りません。
`html-structure` extractor は HTML を読み、それを残します。検索結果は、表の各行を見出し
つきで返し、文書が基準値として印を付けた値を、ラベルと印刷番号つきで返します。結果の
本文がどのページにあるかの印刷番号も返します。

MRAG Plus の同じ名前の変換器の一部を移したもので、`tests/fixtures/html_parity/` の
fixture で同じ出力になることを確かめています。

## 使い始め方

```sh
pip install 'mrag[html]'      # html5lib の解析器
```

`mrag.yaml` に:

```yaml
conversion:
  converter:
    html: html-structure
```

これで `mrag add page.html`(`.htm` も)が、ファイルをこの process の中で読みます。
script は動かさず、ファイルの外は取りに行きません。設定が無ければ、HTML は今までどおり
断ります。profile は Markdown を分割する必要があります(`source_format: markdown`。
`mrag init` の既定)。`text` では構造は付きません。

## 検索結果が返すもの

`mrag search --json`・`POST /api/v1/retrieve`・MCP の `search` は、どの結果にも次の欄を
付けます。構造の記録を持たない文書では、`null` か空です。

- `structured_content` — 結果の本文で、表の各行を `・行の見出し: 「列の見出し」= 値；…` と
  書き、見出し行とその下の罫線の行を落とし、本文に無い表の題を先頭に補ったもの。行の
  見出しは、その行の見出しセル(`th`)で、見出し行の数によりません。見出し行の無い表では、
  値に列の見出しが付きません(`・Beans: 10 to 15；weekly`)。見出しセルの無い行は
  そのまま残ります。検索はこれを読みません。keyword の索引と embedding は、文書が
  書いたとおりの行を読みます。
- `criteria`・`criteria_omitted` — 結果の行にある、文書が `data-criterion` で印を付けた値。
  最大 15 件。それぞれに、`limit`(`data-limit` の `min`・`max`・`range`・`exact`・
  `tolerance`)、`unit`(`data-unit`)、`quantity`(`data-quantity`)、表が置く
  `row_labels` と `column_labels`、`table_caption`、上の見出し `heading`、ファイルの
  ページ `page` と印刷番号 `page_label` が付きます。
- `reference` の `page_start`・`page_end`・`page_label_start`・`page_label_end` —
  本文があるページ。文書が付けたページの区切り(`role="doc-pagebreak"` か
  `epub:type="pagebreak"`。名前は `aria-label`、`title`、その文字)から読みます。

```html
<span role="doc-pagebreak" aria-label="12"></span>
<table><caption>Spacing</caption>
<thead><tr><th>Crop</th><th>Gap</th></tr></thead>
<tr><th>Beans</th><td><span data-criterion data-limit="range" data-unit="cm">10 to 15</span></td></tr>
</table>
```

## 移していないもの

MRAG Plus の `html-structure` は、ほかに定義(`dfn`)とそれを参照する箇所、名前の付いた
要素の `id`、文書の中のリンク、JSON-LD を残し、文書の構造に沿って chunk を切り、
それらを見る `inspect` の command と MCP の resource を持ちます。この branch には、
どれもありません。

## MRAG Plus との意図した違い

- chunk はこの project の chunker が切るので、chunk の本文と、それに沿う
  `structured_content`・その行の基準値は、その切り方に従います。分けた表の部分の
  `structured_content` には、`[Table: …]` の印も入ります。
- Python が改行とみなす、改行以外の文字(`\x0b`・`\x0c`・`\x1c`〜`\x1e`・`\x85`・
  ` `・` `)は、Markdown では空白にします。chunker の行と記録の行を
  揃えるためです。
