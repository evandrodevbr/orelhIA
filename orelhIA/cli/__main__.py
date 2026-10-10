#!/usr/bin/env python3
"""whisper CLI — invoca o server.py como biblioteca para transcrever fora do MCP."""
import argparse
import json
import os
import sys

# server.py fica na raiz do repo, dois níveis acima de orelhIA/cli/.
SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, SERVER_DIR)

import server  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(
        description="Transcreve áudio via Whisper local (Speaches/Whisper)"
    )
    p.add_argument("path", nargs="?", help="Caminho do áudio ou URL (com --url)")
    p.add_argument("-l", "--language", default=None, help="ISO-639-1 (ex.: pt, en)")
    p.add_argument("-m", "--model", default=None, help="Override do modelo")
    p.add_argument(
        "--url", action="store_true", help="Tratar path como URL http(s)"
    )
    p.add_argument(
        "--format",
        choices=["json", "text", "srt", "vtt"],
        default="text",
        help="Formato de saída",
    )
    p.add_argument(
        "--preprocess",
        choices=["none", "vad"],
        default="none",
        help="Pré-processamento: vad remove silêncio antes de transcrever",
    )
    p.add_argument(
        "--health", action="store_true", help="Apenas checar o backend"
    )
    p.add_argument(
        "--metrics", action="store_true", help="Mostrar métricas de uso"
    )
    p.add_argument(
        "--clear-cache", action="store_true", help="Limpar cache de transcrições"
    )
    p.add_argument(
        "--record",
        type=int,
        metavar="SECONDS",
        help="Gravar N segundos do microfone e transcrever",
    )
    p.add_argument(
        "--save",
        metavar="PATH",
        help="Caminho para salvar gravação (com --record)",
    )
    args = p.parse_args()

    if args.health:
        h = server.health()
        print(json.dumps(h, ensure_ascii=False, indent=2))
        return 0 if h.get("ok") else 1

    if args.metrics:
        m = server.get_metrics()
        print(json.dumps(m, ensure_ascii=False, indent=2))
        return 0

    if args.clear_cache:
        r = server.clear_cache()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    if args.record:
        r = server.record_audio(
            args.record,
            output_path=args.save,
            language=args.language,
            model=args.model,
        )
        if args.format == "text":
            print(r.get("text", ""))
        else:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if not r.get("error") else 1

    if not args.path:
        p.print_help()
        return 2

    if args.url:
        r = server.transcribe_url(
            args.path, language=args.language, model=args.model
        )
    else:
        r = server.transcribe_file(
            args.path,
            language=args.language,
            model=args.model,
            response_format=args.format,
            preprocess=args.preprocess,
        )

    if args.format == "text":
        print(r.get("text", ""))
    else:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if not r.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
