<!-- frontend/src/App.vue -->
<template>
  <div id="app-layout">
    <nav class="navbar">
      <!-- 1. 왼쪽 브랜드 로고: 클릭 시 홈 화면으로 이동 -->
      <router-link to="/" class="nav-brand">🤖 RobotData-EC2</router-link>
      
      <!-- 2. 오른쪽 메뉴 영역: 다운로드 센터 버튼 + 로그인/로그아웃 버튼 배치 -->
      <div class="nav-links">
        <router-link to="/download" class="nav-item">다운로드 센터</router-link>
        
      </div>
    </nav>
    <router-view />
  </div>
</template>

<script setup>

</script>

<style>
/* 🎨 공통 전역 및 네비게이션 바 고도화 스타일 */
body {
  margin: 0;
  font-family: 'Noto Sans KR', sans-serif;
  background-color: #f5f7fb;
  color: #333;
}

.navbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #1e293b;
  padding: 15px 40px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

/* 브랜드 로고 스타일 (클릭 가능) */
.nav-brand {
  color: white;
  font-size: 20px;
  font-weight: bold;
  text-decoration: none;
  transition: color 0.2s;
}
.nav-brand:hover {
  color: #38bdf8;
}

.nav-links {
  display: flex;
  align-items: center;
  gap: 20px;
}

/* 다운로드 센터 텍스트 링크 메뉴 */
.nav-item {
  color: #cbd5e1;
  text-decoration: none;
  font-size: 15px;
  font-weight: 500;
  transition: color 0.2s;
}
.nav-item:hover, .nav-item.router-link-active {
  color: #38bdf8;
  font-weight: bold;
}

/* 우측 로그인/로그아웃 버튼 공통 스타일 */
.nav-btn {
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 14px;
  font-weight: bold;
  cursor: pointer;
  text-decoration: none;
  border: none;
  transition: all 0.2s;
}

/* 로그인 버튼 스킨 */
.nav-btn.login {
  background: #3b82f6;
  color: white;
}
.nav-btn.login:hover {
  background: #2563eb;
}

/* 로그아웃 버튼 스킨 */
.nav-btn.logout {
  background: #475569;
  color: #f1f5f9;
}
.nav-btn.logout:hover {
  background: #ef4444;
  color: white;
}

/* 기존 콘텐츠 레이아웃 스타일 보존 */
.page-container {
  max-width: 1000px;
  margin: 40px auto;
  padding: 0 20px;
}
.page-container.center {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 70vh;
}
.card {
  background: white;
  padding: 30px;
  border-radius: 12px;
  box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);
}
.auth-card { width: 360px; }
.main-card { text-align: center; max-width: 500px; }
.input-group { margin-bottom: 15px; display: flex; flex-direction: column; }
.input-group label { margin-bottom: 5px; font-weight: bold; font-size: 14px; }
.input-group input { padding: 10px; border: 1px solid #cbd5e1; border-radius: 6px; }
.btn {
  padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer; font-weight: bold;
  text-decoration: none; display: inline-block; text-align: center;
}
.btn-block { width: 100%; background: #3b82f6; color: white; }
.btn-primary { background: #3b82f6; color: white; }
.btn-success { background: #10b981; color: white; }
.btn-danger { background: #ef4444; color: white; }
.data-table { width: 100%; border-collapse: collapse; text-align: left; }
.data-table th, .data-table td { padding: 15px; border-bottom: 1px solid #e2e8f0; }
.data-table th { background: #f8fafc; }
.badge { background: #e0f2fe; color: #0369a1; padding: 4px 8px; border-radius: 4px; font-size: 12px; }
.empty-state { text-align: center; color: #64748b; padding: 5px 0; }
.header-actions { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.auth-link { margin-top: 15px; text-align: center; font-size: 14px; }
</style>
