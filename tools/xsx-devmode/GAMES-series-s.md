# Jogos para o Xenia UWP no Xbox Series S (Dev Mode)

Fonte: banco de compatibilidade do Xenia Canary (`xenia-canary/game-compatibility`), consultado em
2026-10-26: 1.059 títulos em `state-playable`/`state-gameplay`, 293 deles XBLA (IDs `5841xxxx`).
O estado é medido no PC. No Series S o limite é a memória (~1,3 GB de margem depois que o Xenia
abre, ver `PLAN-memory-series-s.md`), então: XBLA cabe quase sempre; jogos de disco só os leves.

Legenda: **Jogável** = `state-playable`; **Gameplay** = `state-gameplay` (roda com falhas menores).
"vsync off" = o banco pede `vsync = false` no config do título, senão roda devagar.

Confirmados no Series S em 2026-10-25: Castle Crashers (roda), Splinter Cell Conviction (cai por memória).

## Para o menino (5 anos: Star Wars e construir)

| Jogo | Title ID | Estado | Observação |
|---|---|---|---|
| Minecraft: Xbox 360 Edition | 584111F7 | Jogável | Construção; modo criativo |
| Terraria | 5841128F | Gameplay, vsync off | Construção 2D |
| Tower Bloxx Deluxe | 58410956 | Gameplay | Empilhar prédios, um botão |
| Castle Crashers | 584108B7 | Jogável (confirmado) | Coop local |
| Bomberman LIVE | 5841083F | Jogável | Multiplayer local |
| Marble Blast Ultra | 584107D7 | Jogável | Muito leve |
| Sonic CD | 584111F1 | Jogável, vsync off | |
| Sonic Adventure 2 | 58411202 | Jogável, vsync off | |
| Banjo-Kazooie | 58410954 | Jogável, vsync off | Falhas gráficas leves |
| Rayman 3 HD | 5841123F | Gameplay | |
| Peggle | 58410889 | Gameplay | |
| ilomilo | 58410A1B | Gameplay | |
| Frogger | 58410813 | Gameplay | |
| 3D Ultra Minigolf | 58410841 | Jogável, vsync off | |

Star Wars na XBLA: não existe. Opções de **disco**, leves, para testar no Series S:

| Jogo | Title ID | Estado |
|---|---|---|
| LEGO Star Wars: The Complete Saga | 4C4107D7 | Jogável, vsync off |
| LEGO Star Wars II: The Original Trilogy | 4C4107D1 | Jogável (gráficos) |
| LEGO Star Wars III: The Clone Wars | 4C4107F3 | Gameplay |
| Angry Birds Trilogy | 415608E4 | Jogável |
| LEGO Batman | 575207D7 | Jogável |

Alternativa garantida: LEGO Star Wars I e II para **PS2** (ISOs em `~/jogos`) no XBSX2, no mesmo Dev Mode.

## Ação

| Jogo | Title ID | Estado | Observação |
|---|---|---|---|
| Shadow Complex | 584108F6 | Jogável | |
| Perfect Dark | 584109C2 | Jogável | Gráficos com falhas pontuais |
| Portal: Still Alive | 58410960 | Jogável | |
| Castlevania: Symphony of the Night | 58410847 | Jogável | |
| Castlevania: Mirror of Fate HD | 58411408 | Jogável | |
| Halo: Spartan Assault | 58411412 | Jogável | |
| Deadlight | 58410B8D | Jogável | Gráficos com falhas |
| Alien Breed Episode 1 / 2: Assault | 58410964 / 58410A22 | Jogável | |
| Call of Duty Classic | 58410957 | Jogável | `mount_cache = true` |
| GoldenEye 007 XBLA ("Bean") | 584108A9 | Jogável | Build cancelado |
| Wolfenstein 3D | 584109CB | Jogável | |
| Quake Arena Arcade | 584108C8 | Gameplay, vsync off | |
| Super Meat Boy | 58410A5A | Gameplay | |
| Scott Pilgrim vs. the World | 58410A2C | Gameplay | Sem áudio |
| Double Dragon Neon | 58411252 | Gameplay, vsync off | |
| Splinter Cell: Double Agent (disco) | 555307D7 | Jogável | Engine de 2006: candidato a caber no Series S |

## Corrida

| Jogo | Title ID | Estado |
|---|---|---|
| Hydro Thunder Hurricane | 5841096A | Jogável |
| DAYTONA USA | 58410B1D | Jogável |
| Trials HD | 5841095A | Jogável |
| Monster Jam Battlegrounds | 5841148E | Jogável |
| OutRun Online Arcade | 58410968 | Gameplay (áudio ruim) |
| Scrap Metal | 58410987 | Gameplay, vsync off |
| Burnout CRASH! | 58410B5D | Gameplay, vsync off |
| Trials Evolution / Fusion | 58410AAD / 58411401 | Gameplay (gráficos) |

## Estratégia

| Jogo | Title ID | Estado |
|---|---|---|
| Toy Soldiers: Cold War | 58410AAC | Jogável, vsync off |
| Iron Brigade | 58410A95 | Jogável (gráficos) |
| Plants vs Zombies | 584109FF | Gameplay |
| South Park: Let's Go Tower Defense Play! | 58410931 | Gameplay, vsync off |
| Carcassonne | 58410840 | Gameplay |
| Catan | 5841083E | Gameplay |
| Puzzle Quest 2 | 58410A3C | Gameplay |

Tema hacker: nada na XBLA que rode. *Watch Dogs* (555308B7) está em Gameplay mas é disco pesado
com instalação em HD; não cabe no Series S.

## Ordem sugerida para testar o teto de memória

1. Minecraft (XBLA, construção)
2. Hydro Thunder Hurricane (XBLA 3D)
3. Shadow Complex (XBLA, Unreal Engine 3)
4. Splinter Cell: Double Agent (disco, 2006)
5. LEGO Star Wars: The Complete Saga (disco, 2007)

Se o item 4 passar, jogos de disco de 2006–2007 cabem; se cair, o teto é só XBLA até a correção de memória.

## Como colocar no pendrive

- NTFS + ACL (`/usr/local/bin/xbox-copy` ou `setfattr --name=system.ntfs_acl` com o valor do script).
- XBLA: pasta `<TITLEID>/000D0000/<arquivo>` (container LIVE/PIRS), copiada como está.
- Disco: extrair o ISO para pasta com `default.xex` (o frontend não lista ISO em pendrive).
- `gamepaths` do Xenia: `E:/Games/Xbox360`.
