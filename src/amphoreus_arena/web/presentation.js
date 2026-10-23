/* Presentation is independent of game rules and harness protocols.
 * Optional match.presentation.players[side] overrides these defaults. */
window.ArenaPresentation = {
  dsh: {name: 'DeepSeek', avatar: 'web/avatars/deepseek.png', accent: '#68caff', harness: 'DSH DESKTOP'},
  codex: {name: 'GPT', avatar: 'web/avatars/gpt.png', accent: '#c5a5ff', harness: 'CODEX'},
  random: {name: 'Random', avatar: '', accent: '#85d9bb', harness: 'LOCAL TEST'},
  sideNames: {red: '红方', black: '黑方'},
};
window.ArenaRenderers = {};
