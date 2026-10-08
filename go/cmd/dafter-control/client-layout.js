const TILE_GAP = 8;
const TILE_MIN_WIDTH = 160;
const TILE_ASPECT = 16 / 9;

function bestColumns(count, width, height, aspect, gap) {
  let best = { cols: 1, width: 0 };
  for (let cols = 1; cols <= count; cols++) {
    const rows = Math.ceil(count / cols);
    const byWidth = (width - gap * (cols - 1)) / cols;
    const byHeight = ((height - gap * (rows - 1)) / rows) * aspect;
    const tile = Math.min(byWidth, byHeight);
    if (tile > best.width) best = { cols, width: tile };
  }
  return best;
}

function fitTiles() {
  const grid = document.getElementById('video-grid');
  const count = grid.querySelectorAll(':scope > .video-tile').length;
  if (!count) return;
  const style = getComputedStyle(grid);
  const width = grid.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  const height = grid.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
  const { cols, width: tile } = bestColumns(count, width, height, TILE_ASPECT, TILE_GAP);
  grid.style.setProperty('--tile-cols', String(cols));
  grid.style.setProperty('--tile-w', `${Math.max(Math.floor(tile), TILE_MIN_WIDTH)}px`);
}

function watchTileLayout() {
  const grid = document.getElementById('video-grid');
  new ResizeObserver(fitTiles).observe(grid);
  new MutationObserver(fitTiles).observe(grid, { childList: true });
}
