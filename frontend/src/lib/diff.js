/**
 * Minimal line-level diff (LCS-based) for the tool-change viewer.
 *
 * Produces an aligned list of rows for a side-by-side view plus added/removed
 * counts for the inline summary. Inputs are bounded upstream (the backend caps the
 * before/after snapshots), so the O(n·m) dynamic-programming table is cheap here.
 */

/**
 * Compute an aligned line diff between two texts.
 *
 * @param {string} oldText - Previous file content (empty for a newly created file).
 * @param {string} newText - New file content.
 * @returns {{rows: Array<{type: "ctx"|"add"|"del", left?: string, right?: string,
 *   ln?: number, rn?: number}>, added: number, removed: number}}
 *   `rows` is ordered top-to-bottom; `ln`/`rn` are 1-based old/new line numbers.
 */
export function lineDiff(oldText, newText) {
  const a = (oldText || "").split("\n");
  const b = (newText || "").split("\n");
  const n = a.length;
  const m = b.length;

  // LCS length table: dp[i][j] = LCS of a[i:] and b[j:].
  const dp = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j]
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  // Walk the table, emitting context / deletion / addition rows.
  const rows = [];
  let added = 0;
  let removed = 0;
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      rows.push({ type: "ctx", left: a[i], right: b[j], ln: i + 1, rn: j + 1 });
      i++; j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      rows.push({ type: "del", left: a[i], ln: i + 1 }); removed++; i++;
    } else {
      rows.push({ type: "add", right: b[j], rn: j + 1 }); added++; j++;
    }
  }
  while (i < n) { rows.push({ type: "del", left: a[i], ln: i + 1 }); removed++; i++; }
  while (j < m) { rows.push({ type: "add", right: b[j], rn: j + 1 }); added++; j++; }

  return { rows, added, removed };
}
