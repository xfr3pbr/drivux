# Drivux

**Google Drive sob demanda para Linux, com uma interface simples.**

O Drivux mostra todos os arquivos do seu Google Drive numa pasta do computador, como o
Google Drive para computador faz no Windows. Cada arquivo só é baixado quando você abre.
Por baixo, ele usa o [rclone](https://rclone.org); o Drivux cuida da instalação e da
experiência de uso.

## Funcionalidades (v0.1)

- Conecta a conta do Google pelo navegador, sem editar arquivos de configuração.
- Monta o Drive numa pasta (padrão: `~/GoogleDrive`), que liga sozinha com o computador.
- Arquivos abertos ficam guardados em cache (até 10 GB; saem depois de 30 dias sem uso).
- Ao ligar, carrega a lista inteira de pastas de uma vez: depois disso, abrir pastas é instantâneo.
- Alterações no computador sobem em ~1 s; alterações feitas no Drive aparecem em ~10 s.
- Interface GTK4/libadwaita (mesma base visual do Baobab) com status, espaço da conta,
  espaço em cache, pausar, iniciar com o computador e desconectar.
- Notificações do sistema com o que ainda está sincronizando (↑ subindo, ↓ baixando),
  atualizadas no lugar, e aviso separado em caso de erro.
- Linha de comando: `drivux setup | status | start | stop | remove | notify`.

## Compatibilidade

O Drivux foi feito para funcionar em **qualquer distribuição Linux** que tenha:

| Requisito | Por quê |
|---|---|
| **systemd** (serviços do usuário) | mantém a pasta montada e liga sozinho com o computador |
| **FUSE 3** | é o que permite a pasta mostrar arquivos que estão na nuvem |
| **rclone 1.60+** | conversa com o Google Drive |
| **Python 3.11+, PyGObject, GTK 4, libadwaita 1.5+** | interface |

As notificações usam o padrão freedesktop e funcionam em GNOME, KDE, XFCE, Cinnamon,
Hyprland, Sway etc. Caminhos de programas são detectados no sistema, não fixos.

Se faltar algo, o Drivux mostra o comando de instalação certo para a sua distribuição
(apt, dnf, pacman, zypper, apk, xbps, emerge).

| Distribuição | Situação |
|---|---|
| Arch, Manjaro, EndeavourOS, Omarchy | testado (Omarchy/Hyprland) |
| Fedora 40+, openSUSE Tumbleweed | deve funcionar (pacotes recentes) |
| Ubuntu 24.04+, Debian 13+, Mint 22+ | deve funcionar; se o rclone do repositório for antigo, o Drivux indica o instalador oficial |
| Debian 12, Ubuntu 22.04 e anteriores | libadwaita antigo demais para a interface (o `drivux setup` pelo terminal funciona) |
| Sem systemd (Alpine, Void, Devuan, Gentoo com OpenRC) | ainda não suportado |

Instalação das dependências:

```bash
sudo pacman -S rclone fuse3 python-gobject gtk4 libadwaita                       # Arch
sudo dnf install rclone fuse3 python3-gobject gtk4 libadwaita                    # Fedora
sudo apt install rclone fuse3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1             # Ubuntu/Debian
sudo zypper install rclone fuse3 python3-gobject typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1  # openSUSE
```

## Rodando a partir do código

```bash
PYTHONPATH=src python3 -m drivux          # abre a interface
PYTHONPATH=src python3 -m drivux setup    # configura pelo terminal
PYTHONPATH=src python3 -m drivux status
```

Testes:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Como funciona

| Peça | Onde fica |
|---|---|
| Conta do Google (remote `drivux:` do rclone) | `~/.config/rclone/rclone.conf` |
| Configuração do Drivux | `~/.config/drivux/config.json` |
| Serviço que monta a pasta | `~/.config/systemd/user/drivux.service` |
| Serviço das notificações | `~/.config/systemd/user/drivux-notify.service` |
| Cache dos arquivos abertos e log | `~/.cache/drivux/` |
| Canal de controle do rclone (só o seu usuário acessa) | `$XDG_RUNTIME_DIR/drivux.sock` |

Desconectar remove o serviço, o cache e o acesso à conta. **Nada é apagado no Google Drive.**

> **Atenção:** apagar um arquivo **dentro da pasta** apaga no Google Drive também
> (ele vai para a lixeira do Drive).

## Limitações conhecidas

- Documentos nativos do Google (Docs, Planilhas, Formulários) não aparecem na pasta.
- Sem internet, só abrem os arquivos que já estão em cache.
- O acesso padrão do rclone ao Google Drive (client ID compartilhado) será desativado
  em 2026. Para não depender dele, crie um client ID próprio no Google Cloud e informe
  em **Avançado** na tela inicial (ou `drivux setup --client-id ... --client-secret ...`).

## Próximos passos

- [ ] Assistente para criar o client ID próprio no Google Cloud
- [ ] Ícones de status nos arquivos (nuvem / disponível no computador) no Nautilus
- [ ] "Manter off-line" pelo botão direito
- [ ] Ícone na bandeja com progresso e avisos de erro
- [ ] Guardar a lista de pastas no disco para abrir instantâneo já ao ligar
- [ ] Empacotamento para todas as distros (AUR, .deb, .rpm, Flatpak ou AppImage)
- [ ] Suporte a sistemas sem systemd
- [ ] Outros serviços suportados pelo rclone (OneDrive, Dropbox…)

## Licença

GPL-3.0 ou posterior. Veja [LICENSE](LICENSE).
