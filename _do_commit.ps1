$ErrorActionPreference = 'Continue'
# Asegurar identidad local
git config user.name "sergiocontreras-crypto" | Out-Null
git config user.email "308464512+sergiocontreras-crypto@users.noreply.github.com" | Out-Null
# Re-stage por si quedaron temporales nuevos ignorados
git add -A 2>&1 | Out-Null
# Commit inicial
git commit -m "Initial commit: proyecto AXIS (valoracion y proyeccion de portafolio)" 2>&1 | Out-File _commit_result.txt -Encoding utf8
"----LOG----" | Out-File -Append _commit_result.txt
git log --oneline -n 3 2>&1 | Out-File -Append _commit_result.txt
"----FILECOUNT----" | Out-File -Append _commit_result.txt
(git ls-files | Measure-Object -Line).Lines | Out-File -Append _commit_result.txt
