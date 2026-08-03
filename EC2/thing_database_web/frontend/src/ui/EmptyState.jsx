// frontend/src/ui/EmptyState.jsx
//
// 빈 화면·오류 공용.
//
// 빈 목록은 사과가 아니라 안내여야 한다. "아무것도 없습니다" 로 끝내지 않고
// 무엇을 하면 채워지는지 말한다.

export default function EmptyState({ icon: Icon, title, description, children, tone }) {
  return (
    <div className={tone === 'error' ? 'state state-error' : 'state'}>
      {Icon && (
        <span className="state-icon" aria-hidden="true">
          <Icon size={19} strokeWidth={1.7} />
        </span>
      )}
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {children}
    </div>
  );
}
