import { memo } from "react";

const W = 720;
const H = 360;
const BAND_VAR = { 1: "var(--band-g)", 2: "var(--band-r)", 3: "var(--band-i)" };

// Equirectangular projection. RA increases to the left, as on a real sky chart.
const x = (ra) => ((360 - ra) / 360) * W;
const y = (dec) => ((90 - dec) / 180) * H;

function dotRadius(mag) {
  return Math.max(1.2, Math.min(5, 1 + (21 - mag) * 0.9));
}

function SkyMap({ alerts, rules, selected, onSelect }) {
  const cones = rules.filter((r) => r.cone && r.active);
  const newest = alerts.slice(0, 8);

  return (
    <svg
      className="skymap"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={`Sky map with ${alerts.length} recent alerts and ${cones.length} watched regions`}
    >
      <g className="grid">
        {[0, 60, 120, 180, 240, 300].map((ra) => (
          <g key={ra}>
            <line x1={x(ra)} x2={x(ra)} y1={0} y2={H} />
            <text x={x(ra) - 4} y={H - 4} textAnchor="end">{ra / 15}h</text>
          </g>
        ))}
        {[-60, -30, 0, 30, 60].map((dec) => (
          <g key={dec}>
            <line x1={0} x2={W} y1={y(dec)} y2={y(dec)} />
            <text x={4} y={y(dec) - 3}>{dec > 0 ? `+${dec}` : dec}°</text>
          </g>
        ))}
      </g>

      {cones.map((r) => {
        const rDeg = r.cone.radius_arcsec / 3600;
        const stretch = 1 / Math.max(0.05, Math.cos((r.cone.dec * Math.PI) / 180));
        // Very small cones would be invisible; draw at least 7px so people can see them.
        return (
          <ellipse
            key={r.id}
            className="cone"
            cx={x(r.cone.ra)}
            cy={y(r.cone.dec)}
            rx={Math.max(7, ((rDeg * stretch) / 360) * W)}
            ry={Math.max(7, (rDeg / 180) * H)}
          >
            <title>{r.name}</title>
          </ellipse>
        );
      })}

      {alerts
        .slice()
        .reverse()
        .map((a, i, arr) => {
          const age = (arr.length - i) / arr.length; // 0 = newest
          const isSel = a.object_id === selected;
          return (
            <circle
              key={a.candid}
              className={`dot ${isSel ? "selected" : ""}`}
              cx={x(a.ra)}
              cy={y(a.dec)}
              r={dotRadius(a.magpsf)}
              fill={BAND_VAR[a.fid]}
              opacity={a.rb < 0.5 ? 0.2 : 1 - age * 0.75}
              onClick={() => onSelect(a.object_id)}
            >
              <title>{`${a.object_id}  mag ${a.magpsf.toFixed(2)}`}</title>
            </circle>
          );
        })}

      {newest.map((a) => (
        <circle key={`f${a.candid}`} className="flash" cx={x(a.ra)} cy={y(a.dec)} r={2} fill="none"
          stroke={BAND_VAR[a.fid]} />
      ))}
    </svg>
  );
}

export default memo(SkyMap);
