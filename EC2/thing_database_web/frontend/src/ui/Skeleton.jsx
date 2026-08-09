// frontend/src/ui/Skeleton.jsx
//
// 로딩 표시.
//
// 회전하는 원(spinner)은 얼마나 남았는지 알려주지 않고 화면이 완성될 때 내용이
// 튀어 오른다. 들어올 표의 모양을 미리 그려 두면 기다림이 짧게 느껴지고
// 레이아웃이 움직이지 않는다.
//
// 글자(label)는 시각적으로 감추되 DOM 에 남긴다. 스크린 리더가 읽어야 한다.

export default function Skeleton({ rows = 5, label = '불러오고 있습니다…' }) {
  return (
    <div className="skeleton-rows" role="status" aria-live="polite">
      <span style={{
        position: 'absolute', width: 1, height: 1, overflow: 'hidden',
        clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap',
      }}>
        {label}
      </span>
      {Array.from({ length: rows }).map((_, row) => (
        <div className="skeleton-row" key={row} aria-hidden="true">
          {[62, 38, 46, 34, 28].map((width, col) => (
            <span className="shimmer" key={col} style={{ width: `${width}%` }} />
          ))}
        </div>
      ))}
    </div>
  );
}
