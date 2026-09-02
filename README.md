# Annual Report Intelligence

A terminal tool that answers an analyst's questions about a set of annual reports. Every figure in an answer comes from the documents and is cited with file name and page. When the documents do not contain a figure, the tool says so instead of guessing.

The corpus used during development covers BMW, Ford and Tesla reports from 2021 to 2023 plus two unrelated news files, but the tool works with any set of reports registered in `src/ingest/registry.py`.

## Quickstart

Python 3.11 or newer is required.

```
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Add your OpenAI API key to `.env`. `OPENAI_MODEL` can stay at its default. Put the reports under `data/` in the layout the registry expects:

```
data/
  BMW/BMW_Annual_Report_2021.pdf
  BMW/BMW_Annual_Report_2022.pdf
  BMW/BMW_Annual_Report_2023.pdf
  Ford/Ford_Annual_Report_2021.pdf
  Ford/Ford_Annual_Report_2022.pdf
  Ford/Ford_Annual_Report_2023.pdf
  Tesla/Tesla_Annual_Report_2022.pdf
  Tesla/Tesla_Annual_Report_2023.pdf
  news.pdf
  news.docx
```

Then start the app:

```
python -m src.app
```

The first run parses the corpus and builds the index under `index_store/`, which takes a few minutes. Later runs start immediately unless a file in `data/` changes.

Commands inside the app:

| Command | Effect |
|---|---|
| `:sources` | Show the excerpts behind the last answer, with retrieval scores |
| `:reset` | Clear the conversation memory |
| `:quit` | Exit |

Follow-up questions work within a conversation. When the tool rewrites a follow-up into a standalone question, it prints that question in a dim line above the answer.

To run the benchmark:

```
python -m eval.run_eval
```

To run the tests:

```
python -m pytest
```

## How it works

```
data/*.pdf, *.docx
        |
        v
  registry ............ company, year, reporting entity, currency per file
        |
        v
  parse ............... page text, duplicate text layer removal, table detection
        |
        v
  chunk ............... text windows and table chunks, metadata on every chunk
        |
        v
  index ............... text-embedding-3-small vectors in FAISS, chunks in JSON

question
   |  rewrite (uses the last turns to make a follow-up standalone)
   v
retrieve .............. company routing, per-company merge, top-k
   |
   v
answer ................ grounded prompt with excerpt headers, citations checked
```

The pipeline lives in `src/pipeline.py` and is shared by the terminal app and the benchmark runner.

## Design decisions

### Documents are registered, not discovered

Every source file is listed in `src/ingest/registry.py` with its company, reporting year, reporting entity and currency. File names do not always describe the reporting entity: in this corpus the files named `BMW_Annual_Report_2022.pdf` and `BMW_Annual_Report_2023.pdf` are the annual reports of BMW Finance N.V., a financing subsidiary, and contain interest income and a net result for the subsidiary rather than group revenue and profit. The registry records that once, every chunk carries the entity label, and the answering model is told which documents exist and what each one covers. As a result, a question about BMW in 2023 gets an answer that names the subsidiary, gives its figures if they help, and states that group figures for that year are not in the documents.

Unregistered files under `data/` are ignored rather than guessed at, because a chunk without company and entity metadata cannot be cited safely.

### Tables are read from the text layer

The reports use borderless tables that pdfplumber's ruled-table finder returns as nothing, as single-column rows or as fragments. Table detection therefore works on the extracted text: a header line of two or more years, optionally followed by a change column, and rows that end in numeric columns. Each row is re-serialised with its column label attached to every value, so a line such as

```
Group revenues 2 98,282 96,855 104,210 98,990 111,239 12.4
```

becomes

```
Group revenues 2: 2017 = 98,282; 2018 = 96,855; 2019 = 104,210; 2020 = 98,990; 2021 = 111,239; Change in % = 12.4
```

Tables become their own chunks, split by row groups with the header repeated when they are long. This is what lets a question about BMW's 2017 revenue be answered from the five-year overview in the 2021 report, and a question about Ford's 2020 revenue from the comparative column of the 2021 key metrics table.

Each table row is also indexed on its own, as a small child chunk made of the table header, its title and the row. A key metrics table that mixes cash flow, revenue, earnings per share and return on capital embeds as a blur, and for a question about one of those lines the whole table ranked below smaller, more specific chunks. Retrieval matches against the rows and then hands the model the table they belong to, so the answer still comes with the surrounding rows and column labels.

### Duplicate text layers are detected and removed

`Ford_Annual_Report_2023.pdf` renders the text of 74 of its 75 pages twice. On most pages the second rendering is a rigid copy shifted down by about 20 points, but on others it is re-typeset at a slightly different font size, so lines reflow and no geometric shift matches. The detector works on the character stream instead: when a page's opening characters reappear later in the stream and at least 80 percent of what follows repeats text before it, the second run is dropped before any text is read. Nothing is hard-coded to a file. The check runs on every page of every PDF, and no page of the other reports triggers it.

### Every figure is cited, and the citations are checked

The system prompt requires a `(file name, page N)` citation for every figure and forbids figures that are not in the excerpts. After the model answers, the pipeline parses the citations and matches them against the retrieved chunks. The terminal shows the cited locations under the answer, and when an answer cites nothing it shows every excerpt that was consulted instead, so the reader can always see what an answer rests on.

### Follow-ups are handled by query rewriting

The conversation keeps the last four turns. Before retrieval, one model call rewrites the incoming question into a standalone one, so that "and the year before?" after a question about Tesla's 2023 revenue becomes a question about Tesla's 2022 revenue. The rewritten question drives retrieval and is shown in the terminal. A question without history is passed through untouched with no model call.

### Retrieval routes by company, never by year

Company names in the question restrict retrieval to that company's chunks, and multi-company questions are retrieved per company and merged so that a larger report cannot crowd out a smaller one. A question about "which company" or "the companies" without a name is treated as a question about all of them. Years are never used as a filter, because figures for a year often live in a later report's comparative columns or multi-year overview. The one exception is a question about the present state of something ("currently", "latest") with no year named, which is answered from each company's most recent report so that an older status list cannot be merged into the answer.

Ranking fuses two orderings by reciprocal rank: the dense similarity of the embedded question, and BM25 over the same chunks. Dense similarity alone cannot tell the rows of a financial report apart. For "Ford's revenue in 2020" it scored a receivables row above the revenue row, because both are short lines of dollar figures under the same header, and the word that decides the question carried no weight. Three rules on top of the fusion reflect how reports are laid out. A row whose own label names the metric ("Revenue ($M)", "Net income", "Group revenues") gets a bonus, because the metric word in the label is close to decisive while the same word elsewhere in a chunk is weak evidence. For a company-level question, tables whose title names a segment or region are demoted, since regional tables carry the same metric words as the consolidated table and outnumber it. And each company contributes at most two tables with the same title, because reports repeat a table for several regions and years. Metric words are also expanded with their synonyms, since US reports say net income where European reports say net profit, and the registry can declare what a particular entity calls a metric, which is how a financing subsidiary's interest income is found when the question asks about revenue.

## Benchmark results

The benchmark in `eval/questions.py` holds twelve questions an analyst would ask, with reference notes verified against the documents, plus two follow-up sequences that exercise the rewriting step. `eval/run_eval.py` runs them through the full pipeline and writes `eval/results.md` with every answer, its citations and a verdict.

## Limitations

- Retrieval ranks tables by their row labels and titles, which suits questions about named metrics. A question phrased very differently from the report's wording, or about a metric the reports label unusually, can still miss the right chunk.
- Table detection expects year headers. Tables headed by quarters or by text labels are still indexed as running text, which the model can read but which does not get the labelled serialisation.
- The duplicate-layer detector assumes the second rendering follows the first in the character stream. A PDF that interleaves the two renderings line by line would need a different signature.
- Two-column layouts are read line by line, so on a few pages of the BMW Finance N.V. reports a table of contents column is merged into the income statement rows. The figures remain readable but carry stray prefixes.
- Answers are only as good as the excerpts. The model is instructed never to fill gaps from general knowledge, and the benchmark checks for that, but the instruction is a prompt, not a proof.

## Project layout

```
src/
  ingest/registry.py     file -> company, year, entity, currency
  ingest/parse.py        page extraction, duplicate layer removal, table detection
  ingest/chunk.py        structure-aware chunking with metadata
  index/build_index.py   embeddings, FAISS index, manifest
  index/retrieve.py      query parsing, company routing, top-k retrieval
  answer/rewrite.py      standalone question rewriting from conversation history
  answer/answer.py       prompt assembly and citation checking
  pipeline.py            rewrite -> retrieve -> answer, conversation memory
  app.py                 terminal REPL
eval/
  questions.py           benchmark questions with reference notes
  run_eval.py            runs the benchmark and writes results.md
tests/                   parser, dedup, chunking, registry, rewriting
```
