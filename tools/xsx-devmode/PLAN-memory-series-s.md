# Plano técnico: reduzir o footprint de memória do Xenia UWP no Xbox Series S

Alvo: `danprice142/xenia-canary-uwp` (branches `Blades-UWP` e `Blades-UWP-Netplay`),
que reutiliza `src/xenia/memory.cc`, `src/xenia/base/memory_win.cc` e
`src/xenia/cpu/backend/*` do Xenia Canary sem adaptação para o modelo de memória do Xbox.

## 1. Problema observado (medido em 2026-10-25, Series S, Dev Mode, app tipo Game, 1080p)

| Momento | Commit do sistema (`/api/resourcemanager/systemperf`) |
|---|---|
| Console recém-reiniciado | 0,86 GB de 3,1 GB (o limite cresce sob demanda) |
| Xenia Netplay 0.6.0.2 aberto no frontend, sem jogo | **6,8 GB de 8,3 GB** |
| Castle Crashers (XBLA, 146 MB) rodando | 7,0 GB de 8,3 GB (funciona) |
| Splinter Cell Conviction (disco) carregando | 6,9 → 8,3 GB, depois `BaseHeap::Alloc failed to alloc range from host` → crash dump → `Cheap-skate exit` |

- O Xenia loga `UWP app memory usage: 67–636 MiB; limit: 5120 MiB` logo após `Initializing Memory`: o
  contador do app (`MemoryManager::AppMemoryUsage`) **não** enxerga os ~6 GB; o contador de commit do
  sistema enxerga. Ou seja, a reserva do espaço de endereços do guest está sendo cobrada como commit.
- Não é resíduo de execuções anteriores: a medição acima foi feita após reinício limpo.
- Não é tipo App/Game (limite 5120 MiB confirmado), nem resolução (2160p causava outro erro, já resolvido
  com 1080p), nem VCLibs.
- **O custo sobrevive ao processo**: após o crash do Conviction, com `xeniacanary.exe` já encerrado, o
  commit do sistema ficou em 6.003 MiB; só o reinício do console devolve ao baseline (~0,9 GB). Sugere
  que a section nomeada (`Local\xenia_memory_<tick>`) ou as views ficam retidas pelo sistema. Qualquer
  medição precisa começar de um reinício.
- Teste do item 5 da Etapa 1 (texture cache 256/128/16 MB, 2 threads de pipeline) em 2026-10-25:
  **sem efeito** no footprint do frontend (0,9 → 6,8 GB igual ao baseline); o Conviction caiu do mesmo
  jeito e, nessa execução, o console inteiro saiu da rede logo após o crash. Paliativos por config não
  resolvem.
- 2026-10-26, build da CI 0.6.0.3 (`uwp-ci@a5d912b`, mesmo código do 0.6.0.2), Conviction pela `.iso`:
  o jogo passa do boot, cria as threads de áudio (`Dare::*`) e lê `\Default\Sounds\Xbox360\English`,
  depois morre com dois `CRASH DUMP` (PC `0x82E1A910` e `0x82E1A770`,
  `Access Violation: read at 0x0000000100000004`). A base do mapeamento é a primeira tentativa,
  `1ull << 32` (`memory.cc`, `Memory::Initialize`), então o endereço é o guest `0x00000004`: o jogo
  leu um ponteiro nulo + 4, e a página zero é protegida (`protect_zero = true`). Com `log_level = 2`
  **não** aparece `BaseHeap::Alloc failed` nem `AllocFixed failed`, então nenhuma alocação do host
  falhou antes. Hipótese: falha de outra natureza (algum serviço do kernel/XAM devolvendo nulo) ou
  uma alocação do guest que falha sem log. Não houve memwatch nessa execução.

## 2. Onde o custo nasce (código)

1. **Section única de 4,5 GB** — `src/xenia/memory.cc`, `Memory::Initialize()`:
   ```cpp
   mapping_ = xe::memory::CreateFileMappingHandle(file_name_, 0x11FFFFFFF, PageAccess::kReadWrite, /*commit=*/false);
   ```
   Em `memory_win.cc`, `commit=false` vira `SEC_RESERVE` e, no caminho UWP, `CreateFileMappingFromApp`.
   No Windows desktop `SEC_RESERVE` não cobra commit. **Hipótese central: no Xbox (GameOS/SystemOS) a
   section é cobrada integralmente**, ou é cobrada por view mapeada.
2. **Views** — tabela `map_info` + `Memory::MapViews()`: 10 views via `VirtualAlloc2FromApp(MEM_RESERVE_PLACEHOLDER)`
   + `MapViewOfFile3FromApp(MEM_REPLACE_PLACEHOLDER)`. Observação: as views `0x90000000–0x9FFFFFFF` reusam
   o offset `0x80000000`, e todas as views físicas (`0xA0000000–0xFFFFFFFF` e `0x100000000–0x11FFFFFFF`)
   reusam o offset `0x100000000`. Logo o intervalo de arquivo `0x90000000–0xFFFFFFFF` (**1,75 GB**) nunca
   é mapeado: a section poderia ter ~2,75 GB em vez de 4,5 GB sem mudar o layout do guest.
3. **Pré-commit dos 512 MB físicos** — `Memory::Initialize()`, loop `for (i = 1..16) AllocFixed(..., kCommit)`
   (comentário "TODO(Gliniak)"): commita toda a RAM física do 360 na inicialização para o GPU poder ler
   qualquer endereço.
4. **JIT** — `src/xenia/cpu/backend/code_cache_base.h`: `kIndirectionTableSize = 0x1FFFFFFF` (512 MB) e
   `kGeneratedCodeSize = 0x0FFFFFFF` (256 MB). Verificar em `x64_code_cache.cc` se são reservados ou
   commitados de uma vez.
5. **Resto** — texture cache (`texture_cache_memory_limit_soft/hard` = 384/768 MB), shader storage, swap
   chain. Pequeno diante dos itens 1–4, mas é o único que se ajusta sem recompilar.

Conta aproximada: 4,5 GB (1) + 0,5 GB (3) + 0,75 GB (4) + ~0,3 GB (5) ≈ 6 GB, coerente com a medição.

## 3. Etapa 0 — provar a hipótese antes de mexer (sem recompilar o emulador)

Objetivo: saber **qual** contador estoura e **quanto** cada componente custa.

1. **Medidor externo**: script `memwatch.py` (stdlib, mesmo estilo de `devportal.py`) que faz polling de
   `/api/resourcemanager/systemperf` (commit, available) e `/api/resourcemanager/processes`
   (working set do `xeniacanary.exe`) a cada 1 s e grava CSV. Rodar durante: boot do frontend, launch do
   Castle Crashers, launch do Conviction. Resultado esperado: degrau de ~4,5 GB em `Initializing Memory`.
2. **Micro-teste UWP** (projeto mínimo, C++/WinRT, ~50 linhas): criar `CreateFileMappingFromApp` com
   `SEC_RESERVE` de 4 GB, medir commit antes/depois; mapear uma view de 256 MB, medir; commitar 64 MB
   via `VirtualAllocFromApp(MEM_COMMIT)`, medir. Isso decide entre as três variantes da hipótese:
   (a) section cobrada inteira; (b) cada view cobrada; (c) só commit explícito cobrado (então o custo
   vem de outro lugar e a Etapa 1 muda).
3. **Instrumentação no emulador** (quando houver build): logar `MemoryManager::AppMemoryUsage()` e
   `GetProcessMemoryInfo().PagefileUsage` após cada passo de `Memory::Initialize()`, após
   `X64CodeCache::Initialize()` e dentro de `BaseHeap::Alloc` quando `AllocFixed` falhar, com
   `GetLastError()` (esperado `ERROR_COMMITMENT_LIMIT` = 1455 ou `ERROR_NOT_ENOUGH_MEMORY` = 8).

Critério de saída: tabela "componente → MB de commit" e o código de erro real da falha.

## 4. Etapa 1 — correções, em ordem de ganho/esforço

| # | Mudança | Ganho estimado | Esforço | Risco |
|---|---|---|---|---|
| 1 | **Encolher a section**: remapear offsets em `map_info` para que o arquivo tenha só as regiões usadas (virtual 0–0x8FFFFFFF compactado + 512 MB físico) e reduzir o tamanho em `CreateFileMappingHandle`. | ~1,75 GB se (a) | baixo: tabela + constante | baixo; nada muda no layout do guest |
| 2 | **Sections separadas e sob demanda** (`#if XE_PLATFORM_UWP`): uma section por heap (v00000000 1 GB, v40000000 1 GB, v80000000/v90000000 256 MB compartilhada, físico 512 MB) e mapear as views físicas espelhadas na mesma section de 512 MB. Se o Xbox cobra por section, o custo cai para o que o guest realmente usa + 512 MB. | até ~3,5 GB se (a) | médio: `Memory::Initialize/MapViews/UnmapViews`, `TranslateVirtual` continua válido porque os endereços de host das views não mudam | médio; testar MMIO handlers e `LookupHeap` |
| 3 | **Commit lazy do físico** no Xbox: trocar o loop Gliniak por commit sob demanda no `PhysicalHeap::Alloc` (já existe o caminho) e manter o pré-commit só quando `tiled_shared_memory`/readback precisarem. | 0,5 GB | baixo | médio: GPU pode tocar páginas não commitadas → precisa de teste com jogos que usam resolve/readback |
| 4 | **JIT**: confirmar que indireção/código são `MEM_RESERVE` + commit incremental; se forem commitados de uma vez no caminho UWP (`VirtualAllocFromApp` sem `MEM_RESERVE` puro), corrigir. Avaliar `kIndirectionTableSize` menor no Xbox (a tabela cobre 0x80000000–0x9FFFFFFF; só o tamanho do XEX importa). | 0–0,7 GB | baixo | baixo |
| 5 | **Config** (paliativo imediato, sem build): `texture_cache_memory_limit_hard = 256`, `..._soft = 128`, `d3d12_pipeline_creation_threads = 2`, desligar `postprocess_*`. | ~0,3 GB | nenhum | nenhum |

Se a Etapa 0 mostrar a variante (b) (custo por view), a prioridade passa a ser reduzir o **número e tamanho
das views** (fundir as views físicas espelhadas, dropar `0x100000000–0x11FFFFFFF` quando não houver
`readback_resolve`), não o tamanho da section.

## 5. Como compilar e testar

- O port UWP só compila no **Windows** (VS 2022 + workload UWP + Windows SDK 10.0.22621+), pelo projeto em
  `xenia-canary-uwp/` do repositório. Os workflows do GitHub Actions do port (`Windows_x86.yml` no
  Netplay, `Windows_build.yml` no Blades-UWP) geram só o binário **desktop**, não o `.appx`; portanto é
  preciso ou (a) adicionar um job de CI que rode `msbuild` do projeto UWP e `MakeAppx`/`SignTool` com um
  certificado de teste, ou (b) compilar numa VM/PC Windows. A opção (a) é a que permite iterar a partir
  do notebook Linux.
- Pacote resultante: mesma identidade `27d6ba69-…`; publisher diferente exige desinstalar o pacote atual
  via Dev Portal antes de instalar (`DELETE /api/app/packagemanager/package`).
- Loop de teste (já existe em `tools/xsx-devmode`): `devportal.py push/launch/pull`, `memwatch.py`
  (novo) em paralelo, `xenia_log_report.py` no log. Rodar sempre após `POST /api/control/restart`, porque
  relançar o app sem reiniciar deixa o commit inchado e falseia a medição.

## 6. Critério de aceite

1. Commit do sistema com o frontend aberto ≤ **3,5 GB** (hoje 6,8 GB).
2. *Splinter Cell Conviction* chega ao menu principal no Series S a 1080p sem `failed to alloc range from host`.
3. Castle Crashers continua rodando (regressão zero em XBLA).
4. Sem regressão no Series X (medir o mesmo CSV lá, se houver um disponível).

## 7. Fora de escopo por enquanto

- Desempenho de GPU/CPU (ROV indisponível no Series S, tiled resources tier 1): só depois que o jogo
  carregar.
- Blacklist: mais pesado que Conviction; entra como segundo alvo depois do aceite acima.
