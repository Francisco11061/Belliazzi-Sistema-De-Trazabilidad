const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const destination = path.join(root, 'apps/reportes/static/reportes/vendor');
fs.mkdirSync(destination, {recursive: true});
for (const [source, target] of [
    ['dist/chart.umd.min.js', 'chart.umd.min.js'],
    ['LICENSE.md', 'Chart.js-LICENSE.md']
]) {
    fs.copyFileSync(path.join(root, 'node_modules/chart.js', source), path.join(destination, target));
}
console.log('Chart.js local y licencia copiados a static/reportes/vendor.');
