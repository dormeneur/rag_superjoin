---
title: Fact Knowledge Layer
emoji: 📄
colorFrom: gray
colorTo: green
sdk: gradio
app_file: app.py
pinned: false
license: mit
short_description: Grounded facts from PDFs, reconciled across documents
---

# Fact Knowledge Layer

Extracts facts from PDFs, ties every fact to the sentence it came from, and works out
whether facts across documents corroborate, contradict, or can be reconciled.

A difference between two numbers is not called a contradiction until the system has
tried to explain it — by period, unit, currency or scope — and failed. Those verdicts
come from rules, not from a language model, so they are reproducible.

The site opens with six documents already processed: three Delhivery filings and three
institutional reports on the Indian economy. The four cases on the front page are
selected by rule at query time, not hand-picked.

Source and full write-up: https://github.com/dormeneur/rag_superjoin
