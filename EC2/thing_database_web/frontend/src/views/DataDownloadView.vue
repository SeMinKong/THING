<template>
  <div class="page-container">
    <div class="header-actions">
      <h2>💾 모터 로그 파일 다운로드 센터 (실시간 연동)</h2>
      <!-- 💡 불필요해진 이전 로그아웃 버튼 자리를 지워 컴포넌트를 깔끔하게 비웁니다 -->
    </div>

    <!-- 비어있을 때 출력 -->
    <div v-if="fileList.length === 0" class="empty-state card">
      <p>📥 현재 EC2 서버에 수집된 로봇 모터 데이터가 존재하지 않습니다.</p>
      <small>라즈베리파이 장치에서 수집 스크립트(test.py)를 구동하여 데이터를 업로드해 주세요.</small>
    </div>

    <!-- 실제 데이터가 있을 때 출력 -->
    <table v-else class="data-table card">
      <thead>
        <tr>
          <th>로그 ID</th>
          <th>로봇 장비 코드</th>
          <th>파일명</th>
          <th>수집 일시</th>
          <th>파일 다운로드</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="file in fileList" :key="file.id">
          <td>{{ file.id }}</td>
          <td><span class="badge">{{ file.robot_id }}</span></td>
          <td class="file-name">{{ file.file_name }}</td>
          <td>{{ formatDate(file.created_at) }}</td>
          <td>
            <button @click="triggerDownload(file.id)" class="btn btn-success">다운로드</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import api from '../services/api';

  const fileList = ref([]);

// 1. 컴포넌트 마운트 시 Django 백엔드로부터 실제 DB 목록 조회
const fetchFilesFromServer = async () => {
  try {
    const response = await api.get('/motor-data/files/'); // 백엔드 목록 조회 API 호출
    fileList.value = response.data;
  } catch (error) {
    console.error('데이터 로드 실패:', error);
    // 목록 API 가 아직 구현 전인 경우 빈 배열 유지
    fileList.value = [];
  }
};

onMounted(() => {
  fetchFilesFromServer();
});

// 2. 파일 다운로드 트리거 (Presigned URL 연동)
const triggerDownload = async (fileId) => {
  try {
    const response = await api.get(`/motor-data/download/${fileId}/`);
    const downloadUrl = response.data.download_url;
    if (downloadUrl) {
      window.location.href = downloadUrl;
    }
  } catch (error) {
    alert('다운로드 권한이 없거나 만료된 파일입니다.');
  }
};


const formatDate = (dateStr) => {
  if (!dateStr) return '-';
  return new Date(dateStr).toLocaleString();
};
</script>
