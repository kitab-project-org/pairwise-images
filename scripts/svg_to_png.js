// Rasterizes an SVG (read from stdin) to a full-size PNG and a thumbnail PNG.
// Usage: node svg_to_png.js <fullOutPath> <thumbOutPath> <thumbWidth>
const { Resvg } = require("@resvg/resvg-js");
const fs = require("fs");

const [, , fullOut, thumbOut, thumbWidth] = process.argv;
const svg = fs.readFileSync(0, "utf-8");

const full = new Resvg(svg, { fitTo: { mode: "zoom", value: 3 } });
fs.writeFileSync(fullOut, full.render().asPng());

const thumb = new Resvg(svg, { fitTo: { mode: "width", value: Number(thumbWidth) } });
fs.writeFileSync(thumbOut, thumb.render().asPng());
