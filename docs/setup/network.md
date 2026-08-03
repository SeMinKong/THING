# ROS 2 내부망 설정

- 모든 장치에서 같은 `ROS_DOMAIN_ID`를 사용합니다.
- NTP 또는 chrony로 Jetson, Raspberry Pi와 Laptop 시간을 동기화합니다.
- 제어용 네트워크는 프로젝트 내부망으로 제한합니다.
- 장치 IP를 문서에 고정하기 전에 실제 네트워크 담당자와 충돌 여부를 확인합니다.

예시:

```bash
export ROS_DOMAIN_ID=103
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file:///absolute/path/to/cyclonedds.xml
```

`thing_bringup/config/cyclonedds.template.xml`을 장치별 로컬 파일로 복사해 사용하고,
개인 네트워크의 IP 목록은 저장소에 직접 commit하지 않습니다.

확인:

```bash
ros2 multicast receive
ros2 multicast send
ros2 node list
ros2 topic list
```
