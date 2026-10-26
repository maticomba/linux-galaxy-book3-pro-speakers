# Raw capture — Windows driver, 2026-10-26

Galaxy Book3 Pro 14" 940XFG (NP940XFG-KA1AR), Realtek driver 6.0.9689.1, captured with
[`../hda-trace.py`](../hda-trace.py): boot → lock screen → Narrator on/off → shutdown.
5471 verbs in total.

| File | Content |
|---|---|
| `verbs.log.xz` | every CORB verb and RIRB response, timestamped and decoded |
| `coef.log.xz` | COEF accesses on NID 0x20 and one `AMP pack:` line per amplifier write |
| `regs.log.xz` | controller register writes |
| `events.log` | marks (written in Spanish during the session: `inicio de traza` = trace start, `MARCA` = mark, `pantalla-login` = lock screen, `narrador` = Narrator, `apagando` = shutting down) |
| `windows-amp-init.txt` | amplifier writes extracted from `coef.log` (same 398 rows as `fix/windows-amp-init-940xfg-ka1ar.txt`) |

Decompress with `xz -dk *.xz`.
