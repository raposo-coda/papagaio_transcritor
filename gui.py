"""
gui.py — Interface gráfica (tkinter)
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .config import (
    APP_NAME, APP_VERSION, COLORS, LANGS, PROVIDERS,
    SUPPORTED_EXTS, load_config, save_config,
)
from .logger import log
from .pipeline import run_pipeline

C = COLORS


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.resizable(True, True)
        self.minsize(780, 640)
        self.configure(bg=C["bg"])

        self._cfg     = load_config()
        self._files: list = []
        self._queue: queue.Queue = queue.Queue()
        self._running = False

        # Registrar callback do logger na GUI (thread-safe via queue)
        log.set_gui_callback(self._gui_log_callback)

        self._build_ui()
        self._restore_config()
        self._poll_queue()

        self.update_idletasks()
        w, h = 860, 740
        x = (self.winfo_screenwidth()  - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ── Callback do logger ────────────────────────────────────────────────────

    def _gui_log_callback(self, msg: str, tag: str):
        """Recebe mensagens do logger global e coloca na queue da GUI."""
        self._queue.put(("log", (msg, tag)))

    # ── Construção da UI ──────────────────────────────────────────────────────

    def _build_ui(self):
        # Cabeçalho
        header = tk.Frame(self, bg=C["surface"], pady=14)
        header.pack(fill="x")
        tk.Label(header, text=f"  {APP_NAME}",
                 font=("Segoe UI", 17, "bold"),
                 fg=C["accent"], bg=C["surface"]).pack(side="left", padx=18)
        tk.Label(header, text="Transcricao + Diarizacao + Resumo IA",
                 font=("Segoe UI", 9), fg=C["subtext"], bg=C["surface"]).pack(side="left")
        tk.Label(header, text=f"v{APP_VERSION}",
                 font=("Segoe UI", 8), fg=C["muted"], bg=C["surface"]).pack(side="right", padx=18)

        body = tk.Frame(self, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=16, pady=10)

        left = tk.Frame(body, bg=C["bg"])
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))

        # ── Configuração ──────────────────────────────────────────────────────
        self._section(left, "  Configuracao")

        # AssemblyAI key
        tk.Label(left, text="AssemblyAI API Key",
                 font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).pack(anchor="w")
        api_row = tk.Frame(left, bg=C["bg"])
        api_row.pack(fill="x", pady=(2, 8))
        self._api_key = tk.StringVar()
        self._api_entry = tk.Entry(
            api_row, textvariable=self._api_key, show="*",
            font=("Consolas", 10), bg=C["surface"], fg=C["text"],
            insertbackground=C["text"], relief="flat", bd=0,
        )
        self._api_entry.pack(side="left", fill="x", expand=True, ipady=7, ipadx=10)
        self._key_visible = False
        self._show_key_btn = tk.Button(
            api_row, text=" Ver ", font=("Segoe UI", 8),
            bg=C["border"], fg=C["text"],
            activebackground=C["border"], activeforeground=C["text"],
            relief="flat", bd=0, cursor="hand2", command=self._toggle_key,
        )
        self._show_key_btn.pack(side="left", padx=(4, 0), ipady=7, ipadx=4)

        # Idioma + Pasta de saída
        row2 = tk.Frame(left, bg=C["bg"])
        row2.pack(fill="x", pady=(0, 8))

        lc = tk.Frame(row2, bg=C["bg"])
        lc.pack(side="left", expand=True, fill="x", padx=(0, 6))
        tk.Label(lc, text="Idioma", font=("Segoe UI", 9),
                 fg=C["subtext"], bg=C["bg"]).pack(anchor="w")
        self._lang_var = tk.StringVar(value="Português (pt)")
        ttk.Combobox(lc, textvariable=self._lang_var,
                     values=list(LANGS.keys()), state="readonly",
                     font=("Segoe UI", 9)).pack(fill="x", ipady=5)

        oc = tk.Frame(row2, bg=C["bg"])
        oc.pack(side="left", expand=True, fill="x")
        tk.Label(oc, text="Pasta de saida", font=("Segoe UI", 9),
                 fg=C["subtext"], bg=C["bg"]).pack(anchor="w")
        or2 = tk.Frame(oc, bg=C["bg"])
        or2.pack(fill="x")
        self._out_dir = tk.StringVar(value=str(Path.home() / "Transcricoes"))
        tk.Entry(or2, textvariable=self._out_dir, font=("Segoe UI", 9),
                 bg=C["surface"], fg=C["text"],
                 insertbackground=C["text"], relief="flat", bd=0,
                 ).pack(side="left", fill="x", expand=True, ipady=7, ipadx=8)
        self._mkbtn(or2, " ... ", self._pick_output, small=True
                    ).pack(side="left", padx=(4, 0), ipady=7)

        # ── IA para Resumo ────────────────────────────────────────────────────
        self._section(left, "  IA para Resumo")

        prov_row = tk.Frame(left, bg=C["bg"])
        prov_row.pack(fill="x", pady=(0, 4))

        pc = tk.Frame(prov_row, bg=C["bg"])
        pc.pack(side="left", expand=True, fill="x", padx=(0, 6))
        tk.Label(pc, text="Provider", font=("Segoe UI", 9),
                 fg=C["subtext"], bg=C["bg"]).pack(anchor="w")
        self._provider_var = tk.StringVar(value="AssemblyAI LeMUR")
        self._provider_cb = ttk.Combobox(
            pc, textvariable=self._provider_var,
            values=list(PROVIDERS.keys()), state="readonly", font=("Segoe UI", 9),
        )
        self._provider_cb.pack(fill="x", ipady=5)
        self._provider_cb.bind("<<ComboboxSelected>>", self._on_provider_change)

        mc = tk.Frame(prov_row, bg=C["bg"])
        mc.pack(side="left", expand=True, fill="x")
        tk.Label(mc, text="Modelo", font=("Segoe UI", 9),
                 fg=C["subtext"], bg=C["bg"]).pack(anchor="w")
        self._ai_model_var = tk.StringVar(value="(automático)")
        tk.Entry(mc, textvariable=self._ai_model_var, font=("Segoe UI", 9),
                 bg=C["surface"], fg=C["text"],
                 insertbackground=C["text"], relief="flat", bd=0,
                 ).pack(fill="x", ipady=7, ipadx=8)

        # API Key do provider  (sempre visível, enable/disable evita reflow)
        tk.Label(left, text="API Key do Provider", font=("Segoe UI", 9),
                 fg=C["subtext"], bg=C["bg"]).pack(anchor="w")
        ai_key_row = tk.Frame(left, bg=C["bg"])
        ai_key_row.pack(fill="x", pady=(0, 4))
        self._ai_key_var = tk.StringVar()
        self._ai_key_entry = tk.Entry(
            ai_key_row, textvariable=self._ai_key_var, show="*",
            font=("Consolas", 9), bg=C["surface"], fg=C["text"],
            insertbackground=C["text"], relief="flat", bd=0,
        )
        self._ai_key_entry.pack(side="left", fill="x", expand=True, ipady=7, ipadx=8)
        self._ai_key_visible = False
        self._ai_key_toggle = tk.Button(
            ai_key_row, text=" Ver ", font=("Segoe UI", 8),
            bg=C["border"], fg=C["text"],
            activebackground=C["border"], activeforeground=C["text"],
            relief="flat", bd=0, cursor="hand2", command=self._toggle_ai_key,
        )
        self._ai_key_toggle.pack(side="left", padx=(4, 0), ipady=7, ipadx=4)

        # Base URL
        tk.Label(left, text="Base URL", font=("Segoe UI", 9),
                 fg=C["subtext"], bg=C["bg"]).pack(anchor="w")
        self._ai_url_var = tk.StringVar(value="http://localhost:11434")
        self._ai_url_entry = tk.Entry(
            left, textvariable=self._ai_url_var, font=("Segoe UI", 9),
            bg=C["surface"], fg=C["text"],
            insertbackground=C["text"], relief="flat", bd=0,
        )
        self._ai_url_entry.pack(fill="x", ipady=7, ipadx=8, pady=(0, 4))

        self._provider_hint = tk.Label(
            left, text="", font=("Segoe UI", 7, "italic"),
            fg=C["muted"], bg=C["bg"], wraplength=340, justify="left",
        )
        self._provider_hint.pack(anchor="w", pady=(0, 6))

        self._on_provider_change()   # estado inicial dos campos

        # ── Título + Contexto ─────────────────────────────────────────────────
        self._section(left, "  Titulo da Sessao")
        tk.Label(left, text="Nome do relatorio (opcional)",
                 font=("Segoe UI", 8), fg=C["muted"], bg=C["bg"]
                 ).pack(anchor="w", pady=(0, 4))
        self._title_var = tk.StringVar()
        tk.Entry(left, textvariable=self._title_var, font=("Segoe UI", 10),
                 bg=C["surface"], fg=C["text"],
                 insertbackground=C["text"], relief="flat", bd=0,
                 ).pack(fill="x", ipady=7, ipadx=10, pady=(0, 4))

        self._section(left, "  Prompt de Contexto")
        tk.Label(left, text="Contexto, participantes ou foco da analise (opcional)",
                 font=("Segoe UI", 8), fg=C["muted"], bg=C["bg"]
                 ).pack(anchor="w", pady=(0, 4))
        self._ctx_box = scrolledtext.ScrolledText(
            left, height=5, wrap="word", font=("Segoe UI", 9),
            bg=C["surface"], fg=C["text"],
            insertbackground=C["text"], relief="flat", bd=0, padx=8, pady=6,
        )
        self._ctx_box.pack(fill="both", expand=True)

        # ── Arquivos ──────────────────────────────────────────────────────────
        self._section(left, "  Arquivos")
        fc = tk.Frame(left, bg=C["bg"])
        fc.pack(fill="x", pady=(0, 4))
        self._mkbtn(fc, "+ Adicionar", self._add_files).pack(side="left")
        self._mkbtn(fc, "x Remover",  self._remove_file, danger=True).pack(side="left", padx=6)
        self._mkbtn(fc, "↑ Subir",    self._move_up,   small=True).pack(side="left")
        self._mkbtn(fc, "↓ Descer",   self._move_down, small=True).pack(side="left", padx=(4, 0))

        lf = tk.Frame(left, bg=C["border"], padx=1, pady=1)
        lf.pack(fill="both", expand=True)
        self._file_lb = tk.Listbox(
            lf, bg=C["surface"], fg=C["text"],
            selectbackground=C["accent"], selectforeground="#fff",
            font=("Segoe UI", 9), relief="flat", bd=0, activestyle="none",
        )
        self._file_lb.pack(fill="both", expand=True, padx=2, pady=2)

        # Botão iniciar
        bf = tk.Frame(left, bg=C["bg"], pady=10)
        bf.pack(fill="x")
        self._run_btn = tk.Button(
            bf, text="  Iniciar Transcricao",
            font=("Segoe UI", 11, "bold"),
            bg=C["accent"], fg="#fff",
            activebackground=C["accent2"], activeforeground="#fff",
            relief="flat", bd=0, cursor="hand2", pady=11,
            command=self._start,
        )
        self._run_btn.pack(fill="x")
        self._progress = ttk.Progressbar(bf, mode="indeterminate")
        self._progress.pack(fill="x", pady=(6, 0))

        # ── Painel direito: log ───────────────────────────────────────────────
        right = tk.Frame(body, bg=C["bg"], width=300)
        right.pack(side="right", fill="both")
        right.pack_propagate(False)

        self._section(right, "  Log")
        self._log_box = scrolledtext.ScrolledText(
            right, wrap="word", font=("Consolas", 8),
            bg=C["surface"], fg=C["text"],
            insertbackground=C["text"], relief="flat", bd=0, padx=8, pady=6,
            state="disabled",
        )
        self._log_box.pack(fill="both", expand=True)
        self._log_box.tag_config("ok",   foreground=C["success"])
        self._log_box.tag_config("warn", foreground=C["warning"])
        self._log_box.tag_config("err",  foreground=C["error"])
        self._log_box.tag_config("head", foreground=C["accent"])
        self._log_box.tag_config("dim",  foreground=C["muted"])

        # Botões de log
        log_btns = tk.Frame(right, bg=C["bg"])
        log_btns.pack(fill="x", pady=(4, 0))
        self._mkbtn(log_btns, "Limpar", self._clear_log, small=True).pack(side="left")
        self._mkbtn(log_btns, "Abrir arquivo de log", self._open_log_file, small=True
                    ).pack(side="right")

        # Status bar
        sb = tk.Frame(self, bg=C["surface"], pady=4)
        sb.pack(fill="x", side="bottom")
        self._status = tk.StringVar(value="Pronto.")
        tk.Label(sb, textvariable=self._status,
                 font=("Segoe UI", 8), fg=C["subtext"], bg=C["surface"]
                 ).pack(side="left", padx=12)
        # Exibe caminho do log atual
        self._log_path_var = tk.StringVar(value="")
        tk.Label(sb, textvariable=self._log_path_var,
                 font=("Segoe UI", 7), fg=C["muted"], bg=C["surface"]
                 ).pack(side="right", padx=12)

    # ── Helpers de UI ────────────────────────────────────────────────────────

    def _section(self, parent, title):
        f = tk.Frame(parent, bg=C["bg"])
        f.pack(fill="x", pady=(10, 4))
        tk.Label(f, text=title, font=("Segoe UI", 9, "bold"),
                 fg=C["accent"], bg=C["bg"]).pack(side="left")
        tk.Frame(f, bg=C["border"], height=1).pack(
            side="left", fill="x", expand=True, padx=(8, 0), pady=6)

    def _mkbtn(self, parent, text, cmd, small=False, danger=False):
        bg = C["error"] if danger else (C["border"] if small else C["accent"])
        return tk.Button(
            parent, text=text, command=cmd,
            font=("Segoe UI", 8 if small else 9),
            bg=bg, fg="#fff",
            activebackground=C["error"] if danger else C["accent2"],
            activeforeground="#fff",
            relief="flat", bd=0, cursor="hand2", padx=8, pady=4,
        )

    # ── Handlers de provider ──────────────────────────────────────────────────

    def _on_provider_change(self, event=None):
        """
        Habilita/desabilita campos conforme provider selecionado.
        Não altera o layout para evitar reflow que apaga ttk.Combobox readonly.
        """
        name = self._provider_var.get()
        cfg  = PROVIDERS.get(name, {})

        self._ai_model_var.set(cfg.get("default_model", ""))

        key_state = "normal" if cfg.get("needs_key") else "disabled"
        key_bg    = C["surface"] if cfg.get("needs_key") else C["border"]
        self._ai_key_entry.config(state=key_state, bg=key_bg)
        self._ai_key_toggle.config(state=key_state)

        url_state = "normal" if cfg.get("needs_url") else "disabled"
        url_bg    = C["surface"] if cfg.get("needs_url") else C["border"]
        self._ai_url_entry.config(state=url_state, bg=url_bg)
        if cfg.get("needs_url") and not self._ai_url_var.get():
            self._ai_url_var.set(
                "http://localhost:11434" if cfg.get("id") == "ollama" else ""
            )

        self._provider_hint.config(text=cfg.get("hint", ""))

    def _toggle_key(self):
        self._key_visible = not self._key_visible
        self._api_entry.config(show="" if self._key_visible else "*")
        self._show_key_btn.config(text=" Ocultar " if self._key_visible else " Ver ")

    def _toggle_ai_key(self):
        self._ai_key_visible = not self._ai_key_visible
        self._ai_key_entry.config(show="" if self._ai_key_visible else "*")
        self._ai_key_toggle.config(text=" Ocultar " if self._ai_key_visible else " Ver ")

    # ── Handlers de arquivo ───────────────────────────────────────────────────

    def _pick_output(self):
        d = filedialog.askdirectory(title="Selecionar pasta de saida")
        if d:
            self._out_dir.set(d)

    def _add_files(self):
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED_EXTS))
        paths = filedialog.askopenfilenames(
            title="Selecionar arquivos",
            filetypes=[("Video / Audio", exts), ("Todos", "*.*")],
        )
        added = 0
        for p in paths:
            fp = Path(p)
            if fp not in self._files:
                self._files.append(fp)
                self._file_lb.insert("end", fp.name)
                added += 1
        if added:
            self._status.set(f"{added} arquivo(s) adicionado(s). Total: {len(self._files)}")

    def _remove_file(self):
        sel = self._file_lb.curselection()
        if not sel:
            return
        for i in reversed(sel):
            self._files.pop(i)
            self._file_lb.delete(i)
        self._status.set(f"Removido. Total: {len(self._files)} arquivo(s).")

    def _move_up(self):
        sel = self._file_lb.curselection()
        if not sel or sel[0] == 0:
            return
        i = sel[0]
        self._files[i], self._files[i - 1] = self._files[i - 1], self._files[i]
        self._refresh_list(i - 1)

    def _move_down(self):
        sel = self._file_lb.curselection()
        if not sel or sel[0] >= len(self._files) - 1:
            return
        i = sel[0]
        self._files[i], self._files[i + 1] = self._files[i + 1], self._files[i]
        self._refresh_list(i + 1)

    def _refresh_list(self, select=None):
        self._file_lb.delete(0, "end")
        for f in self._files:
            self._file_lb.insert("end", f.name)
        if select is not None:
            self._file_lb.selection_set(select)
            self._file_lb.see(select)

    # ── Start pipeline ────────────────────────────────────────────────────────

    def _start(self):
        if self._running:
            return

        api_key = self._api_key.get().strip()
        if not api_key:
            messagebox.showerror(
                "API Key ausente",
                "Informe a API Key da AssemblyAI.\n"
                "Cadastre-se em: https://www.assemblyai.com/",
            )
            return

        if not self._files:
            messagebox.showerror("Sem arquivos", "Adicione pelo menos um arquivo.")
            return

        lang_label  = self._lang_var.get()
        lang_code   = LANGS.get(lang_label, "pt")
        out_dir     = Path(self._out_dir.get()).expanduser().resolve()
        ctx         = self._ctx_box.get("1.0", "end").strip()
        title       = self._title_var.get().strip()
        prov_name   = self._provider_var.get()
        prov_cfg    = PROVIDERS.get(prov_name, {})
        provider_id = prov_cfg.get("id", "lemur")
        ai_model    = self._ai_model_var.get().strip()
        ai_key      = self._ai_key_var.get().strip()
        ai_url      = self._ai_url_var.get().strip()

        save_config({
            "api_key":  api_key,
            "lang":     lang_label,
            "out_dir":  str(out_dir),
            "provider": prov_name,
            "ai_model": ai_model,
            "ai_key":   ai_key,
            "ai_url":   ai_url,
        })

        self._running = True
        self._run_btn.config(state="disabled", text="  Processando...")
        self._progress.start(12)

        threading.Thread(
            target=run_pipeline,
            kwargs=dict(
                file_paths=list(self._files),
                lang_code=lang_code,
                api_key=api_key,
                context_prompt=ctx,
                title=title,
                output_dir=out_dir,
                on_done=lambda od, fp: self._queue.put(("done", (od, fp))),
                on_error=lambda e: self._queue.put(("error", e)),
                provider_id=provider_id,
                ai_model=ai_model,
                ai_key=ai_key,
                ai_url=ai_url,
            ),
            daemon=True,
        ).start()

    # ── Log na GUI ────────────────────────────────────────────────────────────

    def _write_log(self, msg: str, tag: str = ""):
        """Escreve uma linha no painel de log da GUI. Chamado sempre na thread principal."""
        self._log_box.config(state="normal")
        ts   = datetime.now().strftime("%H:%M:%S")
        # Inferir tag se não fornecida
        if not tag:
            low = msg.lower()
            if any(k in low for k in ("[ok]", "concluido", "salvo", "gerado")):
                tag = "ok"
            elif any(k in low for k in ("[warn]", "aviso")):
                tag = "warn"
            elif any(k in low for k in ("[erro]", "falhou", "erro", "error")):
                tag = "err"
            elif any(k in low for k in ("===", "iniciando", "pipeline")):
                tag = "head"
        self._log_box.insert("end", f"[{ts}] {msg}\n", tag or "")
        self._log_box.see("end")
        self._log_box.config(state="disabled")

    def _clear_log(self):
        self._log_box.config(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.config(state="disabled")

    def _open_log_file(self):
        lf = log.log_file
        if not lf or not lf.exists():
            messagebox.showinfo("Log", "Nenhum arquivo de log disponivel ainda.")
            return
        self._open_folder(lf.parent)

    # ── Poll da queue ─────────────────────────────────────────────────────────

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    msg, tag = payload
                    self._write_log(msg, tag)
                    self._status.set(msg.strip()[:90])
                elif kind == "done":
                    self._on_done(*payload)
                elif kind == "error":
                    self._on_error(payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _on_done(self, out_dir, final_file):
        self._running = False
        self._progress.stop()
        self._run_btn.config(state="normal", text="  Iniciar Transcricao")
        self._write_log(f"Concluido! Pasta: {out_dir}", "ok")
        self._status.set("Concluído!")
        if log.log_file:
            self._log_path_var.set(f"log: {log.log_file.name}")
        if messagebox.askyesno(
            "Concluído!",
            f"Transcricao finalizada!\n\n"
            f"Relatorio: {final_file.name}\n\n"
            f"Abrir pasta de saida?",
        ):
            self._open_folder(out_dir)

    def _on_error(self, msg: str):
        self._running = False
        self._progress.stop()
        self._run_btn.config(state="normal", text="  Iniciar Transcricao")
        self._write_log(f"ERRO: {msg}", "err")
        self._status.set("Erro — veja o log")
        if log.log_file:
            self._log_path_var.set(f"log: {log.log_file.name}")
        messagebox.showerror(
            "Erro",
            f"{msg}\n\nLog completo salvo em:\n{log.log_file or 'N/A'}",
        )

    def _open_folder(self, path):
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass

    # ── Config ────────────────────────────────────────────────────────────────

    def _restore_config(self):
        self._api_key.set(self._cfg.get("api_key", ""))
        lang = self._cfg.get("lang", "Português (pt)")
        if lang in LANGS:
            self._lang_var.set(lang)
        if out := self._cfg.get("out_dir"):
            self._out_dir.set(out)
        prov = self._cfg.get("provider", "AssemblyAI LeMUR")
        if prov in PROVIDERS:
            self._provider_var.set(prov)
        if m := self._cfg.get("ai_model"):
            self._ai_model_var.set(m)
        self._ai_key_var.set(self._cfg.get("ai_key", ""))
        self._ai_url_var.set(self._cfg.get("ai_url", ""))
        self._on_provider_change()
