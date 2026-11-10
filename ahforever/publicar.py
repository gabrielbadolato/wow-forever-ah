import os
import shutil
import subprocess
from datetime import datetime

RAMO = "gh-pages"
# Os commits são automáticos; a máquina não tem identidade global do git.
AUTOR = ("-c", "user.name=Monitor AH Forever", "-c", "user.email=ah-forever@localhost")


def _git(pasta, env, *args, check=True):
    r = subprocess.run(["git", *AUTOR, "-C", str(pasta), *args], env=env,
                       capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"pages: git {args[0]} falhou: {r.stderr.strip()}")
    return r


def publicar(cfg):
    """Envia <pasta_site> para o ramo gh-pages do repositório configurado."""
    if not cfg.repositorio_pages:
        return
    pasta = cfg.pasta_pages
    env = {**os.environ, "GIT_SSH_COMMAND":
           f"ssh -i '{cfg.chave_pages}' -o IdentitiesOnly=yes -o BatchMode=yes"}
    if not (pasta / ".git").exists():
        pasta.mkdir(parents=True, exist_ok=True)
        _git(pasta, env, "init", "-q", "-b", RAMO)
        _git(pasta, env, "remote", "add", "origin", cfg.repositorio_pages)
    _git(pasta, env, "remote", "set-url", "origin", cfg.repositorio_pages)

    # Cópia limpa: o que saiu do site também sai da publicação.
    for antigo in pasta.iterdir():
        if antigo.name != ".git":
            shutil.rmtree(antigo) if antigo.is_dir() else antigo.unlink()
    shutil.copytree(cfg.pasta_site, pasta, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("*.tmp"))
    # Sem isto o Pages passa os arquivos pelo Jekyll.
    (pasta / ".nojekyll").touch()

    _git(pasta, env, "add", "-A")
    tem_commit = _git(pasta, env, "rev-parse", "-q", "--verify", "HEAD", check=False).returncode == 0
    if tem_commit and _git(pasta, env, "diff", "--cached", "--quiet", check=False).returncode == 0:
        print("pages: nada mudou")
        return
    # Um commit só, reescrito a cada vez: os dados mudam toda hora e o
    # histórico deles fica no banco, não no git.
    mensagem = f"Dados de {datetime.now():%d/%m/%Y %H:%M}"
    _git(pasta, env, "commit", "-q", "-m", mensagem, *(["--amend"] if tem_commit else []))
    _git(pasta, env, "push", "-q", "--force", "origin", RAMO)
    print(f"pages: publicado ({mensagem})")
