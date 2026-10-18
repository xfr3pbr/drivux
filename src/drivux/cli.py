"""Linha de comando do Drivux. Sem argumentos, abre a interface."""

import argparse
import sys
from pathlib import Path

from . import config, notifier, rclone, service


def cmd_setup(args) -> int:
    issues = rclone.problems()
    if issues:
        print("\n\n".join(issues), file=sys.stderr)
        return 1
    folder = Path(args.folder).expanduser().resolve()
    service.check_folder(folder)
    print("Abrindo o navegador: entre com a sua conta do Google e clique em Permitir.")
    rclone.create_remote(rclone.REMOTE, args.client_id or "", args.client_secret or "")
    cfg = config.Config(remote=rclone.REMOTE, folder=folder)
    config.save(cfg)
    service.install(cfg)
    print(f"Pronto. O Google Drive aparece em {folder}")
    return 0


def cmd_status(_args) -> int:
    cfg = config.load()
    if not cfg:
        print("Drivux não está configurado. Rode: drivux setup")
        return 1
    st = service.status()
    print(f"Pasta:        {cfg.folder}")
    print(f"Conectado:    {'sim' if st.active else 'não (pausado)'}")
    print(f"Ao ligar:     {'inicia sozinho' if st.enabled else 'não inicia'}")
    print(f"Notificações: {'ligadas' if st.notifications else 'desligadas'}")
    if st.active:
        print(f"Pastas:       {'carregando…' if st.loading_folders else 'carregadas'}")
        print(f"Enviando:     {st.uploading + st.queued} arquivo(s)")
        print(f"Cache local:  {st.cache_used / 1024**2:.0f} MB")
    return 0


def cmd_start(_args) -> int:
    service.start()
    return 0


def cmd_stop(_args) -> int:
    service.stop()
    return 0


def cmd_notify(_args) -> int:
    notifier.run(service.socket_path())
    return 0


def cmd_remove(_args) -> int:
    cfg = config.load()
    service.uninstall()
    if cfg:
        rclone.delete_remote(cfg.remote)
    config.remove()
    print("Drivux desconectado. Nada foi apagado no Google Drive.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="drivux", description="Google Drive sob demanda para Linux.")
    sub = parser.add_subparsers(dest="command")

    setup = sub.add_parser("setup", help="conecta a conta do Google e monta a pasta")
    setup.add_argument("--folder", default=str(config.DEFAULT_FOLDER), help="pasta do Drive (padrão: ~/GoogleDrive)")
    setup.add_argument("--client-id", help="client ID próprio do Google Cloud (opcional)")
    setup.add_argument("--client-secret", help="client secret do client ID próprio")
    setup.set_defaults(func=cmd_setup)

    sub.add_parser("status", help="mostra o estado da sincronização").set_defaults(func=cmd_status)
    sub.add_parser("start", help="liga a pasta ao Drive").set_defaults(func=cmd_start)
    sub.add_parser("stop", help="pausa (desliga a pasta do Drive)").set_defaults(func=cmd_stop)
    sub.add_parser("remove", help="desconecta a conta e remove o serviço").set_defaults(func=cmd_remove)
    sub.add_parser("notify", help="mostra notificações do que está sincronizando").set_defaults(func=cmd_notify)

    args = parser.parse_args(argv)
    if not args.command:
        from .app import main as gui_main

        return gui_main()
    try:
        return args.func(args)
    except (ValueError, rclone.RcloneError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
