#!/usr/bin/env bash
# Jetson 호스트에서 Vision, Logger/Uploader, Web Bridge, Vite를 한 번에 실행한다.
# Windows에서는 SSH로 Jetson에 접속한 뒤 이 파일만 실행하면 된다.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
FRONTEND_DIR="${REPO_DIR}/web/frontend"

CONTAINER_NAME="${THING_CONTAINER_NAME:-thing-jetson-full}"
CONTAINER_REPO_DIR="${THING_CONTAINER_REPO_DIR:-/home/c103/S15P11C103}"
VITE_HOST="${THING_VITE_HOST:-0.0.0.0}"
VITE_PORT="${THING_VITE_PORT:-5173}"
ROS_PID_FILE="/tmp/thing-vision-web-ros.pid"

ROS_PROCESS_PID=""
VITE_PROCESS_PID=""
ROS_CHILD_PIDS=()
STOPPING=0

print_usage() {
    cat <<EOF
Usage: $(basename "$0")

Environment overrides:
  THING_CONTAINER_NAME       Docker container name (default: thing-jetson-full)
  THING_CONTAINER_REPO_DIR   Repository path inside Docker
  THING_VITE_HOST            Vite bind address (default: 0.0.0.0)
  THING_VITE_PORT            Vite port (default: 5173)
EOF
}

stop_host_processes() {
    if (( STOPPING )); then
        return
    fi
    STOPPING=1

    trap - HUP INT TERM EXIT
    printf '\n전체 서비스를 종료합니다.\n'

    if [[ -n "${VITE_PROCESS_PID}" ]] && kill -0 "${VITE_PROCESS_PID}" 2>/dev/null; then
        kill -TERM "${VITE_PROCESS_PID}" 2>/dev/null || true
    fi
    # sudo/docker CLI가 시그널을 가로채더라도 컨테이너 내부 관리자 PID에
    # 직접 SIGTERM을 보내 ROS launch와 자식 노드가 남지 않게 한다.
    if sudo docker inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
        sudo docker exec "${CONTAINER_NAME}" bash -c \
            'if [[ -r /tmp/thing-vision-web-ros.pid ]]; then read -r pid < /tmp/thing-vision-web-ros.pid; kill -TERM "$pid" 2>/dev/null || true; fi' \
            >/dev/null 2>&1 || true
    fi
    if [[ -n "${ROS_PROCESS_PID}" ]] && kill -0 "${ROS_PROCESS_PID}" 2>/dev/null; then
        kill -TERM "${ROS_PROCESS_PID}" 2>/dev/null || true
    fi

    [[ -z "${VITE_PROCESS_PID}" ]] || wait "${VITE_PROCESS_PID}" 2>/dev/null || true
    [[ -z "${ROS_PROCESS_PID}" ]] || wait "${ROS_PROCESS_PID}" 2>/dev/null || true
}

stop_ros_processes() {
    if (( STOPPING )); then
        return
    fi
    STOPPING=1

    trap - HUP INT TERM EXIT
    for pid in "${ROS_CHILD_PIDS[@]}"; do
        if kill -0 "${pid}" 2>/dev/null; then
            kill -TERM "${pid}" 2>/dev/null || true
        fi
    done
    for pid in "${ROS_CHILD_PIDS[@]}"; do
        wait "${pid}" 2>/dev/null || true
    done
    rm -f "${ROS_PID_FILE}"
}

run_ros_stack_in_container() {
    local container_workspace="${CONTAINER_REPO_DIR}/thing_ws"

    if [[ ! -f /opt/ros/humble/install/setup.bash ]]; then
        echo "오류: /opt/ros/humble/install/setup.bash가 없습니다." >&2
        exit 1
    fi
    if [[ ! -f "${container_workspace}/install/setup.bash" ]]; then
        echo "오류: ${container_workspace}/install/setup.bash가 없습니다. 먼저 colcon build를 실행하세요." >&2
        exit 1
    fi

    # Docker 생성 시 지정한 값이 있으면 유지하고, 없을 때만 프로젝트 기본값을 쓴다.
    export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-103}"
    export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
    export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
    if [[ -z "${CYCLONEDDS_URI:-}" && -f /home/c103/cyclonedds.xml ]]; then
        export CYCLONEDDS_URI="file:///home/c103/cyclonedds.xml"
    fi

    # ROS/colcon setup 스크립트는 COLCON_TRACE 같은 미설정 변수를 확인하므로
    # nounset 검사를 잠시 끈 뒤, 환경 로딩이 끝나면 다시 활성화한다.
    set +u
    # shellcheck disable=SC1091
    source /opt/ros/humble/install/setup.bash
    # shellcheck disable=SC1091
    source "${container_workspace}/install/setup.bash"
    set -u
    cd "${container_workspace}"

    if [[ -r "${ROS_PID_FILE}" ]]; then
        read -r existing_pid < "${ROS_PID_FILE}" || true
        if [[ -n "${existing_pid:-}" ]] && kill -0 "${existing_pid}" 2>/dev/null; then
            echo "오류: 통합 ROS 서비스가 이미 실행 중입니다(PID ${existing_pid})." >&2
            exit 1
        fi
        rm -f "${ROS_PID_FILE}"
    fi
    printf '%s\n' "$$" > "${ROS_PID_FILE}"

    trap stop_ros_processes HUP INT TERM EXIT

    echo "[ROS] Vision을 시작합니다."
    ros2 launch thing_bringup vision.launch.py &
    ROS_CHILD_PIDS+=("$!")

    echo "[ROS] Logger와 Uploader를 시작합니다."
    ros2 launch thing_bringup logger.launch.py &
    ROS_CHILD_PIDS+=("$!")

    echo "[ROS] MJPEG와 Web Bridge를 시작합니다."
    ros2 launch thing_bringup web_bridge.launch.py &
    ROS_CHILD_PIDS+=("$!")

    # 구성요소 하나라도 종료되면 남은 프로세스도 종료해 반쪽 실행을 방지한다.
    set +e
    wait -n "${ROS_CHILD_PIDS[@]}"
    local status=$?
    set -e

    if (( STOPPING == 0 )); then
        echo "오류: ROS 구성요소 하나가 종료되어 전체 서비스를 종료합니다." >&2
    fi
    return "${status}"
}

run_from_jetson_host() {
    if [[ ! -f "${FRONTEND_DIR}/package.json" ]]; then
        echo "오류: ${FRONTEND_DIR}/package.json을 찾을 수 없습니다." >&2
        exit 1
    fi

    # `ssh host "command"`는 비대화형 셸이라 ~/.bashrc의 NVM 설정을
    # 건너뛸 수 있다. npm 검사 전에 NVM을 명시적으로 불러온다.
    if ! command -v npm >/dev/null 2>&1; then
        export NVM_DIR="${NVM_DIR:-${HOME}/.nvm}"
        if [[ -s "${NVM_DIR}/nvm.sh" ]]; then
            # shellcheck disable=SC1091
            source "${NVM_DIR}/nvm.sh"
        fi
    fi
    if ! command -v npm >/dev/null 2>&1; then
        echo "오류: Jetson 호스트에서 npm을 찾을 수 없습니다. NVM 또는 Node.js 설치를 확인하세요." >&2
        exit 1
    fi
    if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
        echo "오류: frontend 의존성이 없습니다. ${FRONTEND_DIR}에서 npm install을 먼저 실행하세요." >&2
        exit 1
    fi
    if ! command -v sudo >/dev/null 2>&1 || ! command -v docker >/dev/null 2>&1; then
        echo "오류: sudo 또는 docker 명령을 찾을 수 없습니다." >&2
        exit 1
    fi

    # 시작 시 한 번만 sudo 인증을 받아 백그라운드 docker exec가 중간에 멈추지 않게 한다.
    sudo -v

    if ! sudo docker inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
        echo "오류: Docker 컨테이너 '${CONTAINER_NAME}'를 찾을 수 없습니다." >&2
        exit 1
    fi
    if [[ "$(sudo docker inspect -f '{{.State.Running}}' "${CONTAINER_NAME}")" != "true" ]]; then
        echo "Docker 컨테이너 '${CONTAINER_NAME}'를 시작합니다."
        sudo docker start "${CONTAINER_NAME}" >/dev/null
    fi

    trap stop_host_processes HUP INT TERM EXIT

    echo "Docker에서 ROS 2 서비스를 시작합니다."
    sudo docker exec -i \
        -e THING_CONTAINER_REPO_DIR="${CONTAINER_REPO_DIR}" \
        "${CONTAINER_NAME}" \
        bash "${CONTAINER_REPO_DIR}/scripts/run_jetson_vision_web.sh" --ros-only &
    ROS_PROCESS_PID=$!

    echo "Jetson 호스트에서 Vite를 시작합니다: http://${VITE_HOST}:${VITE_PORT}"
    (
        cd "${FRONTEND_DIR}"
        exec npm run dev -- --host "${VITE_HOST}" --port "${VITE_PORT}"
    ) &
    VITE_PROCESS_PID=$!

    echo "서비스가 실행 중입니다. 전체 종료는 Ctrl+C를 누르세요."

    set +e
    wait -n "${ROS_PROCESS_PID}" "${VITE_PROCESS_PID}"
    local status=$?
    set -e

    if (( STOPPING == 0 )); then
        echo "오류: 구성요소 하나가 종료되어 전체 서비스를 종료합니다." >&2
    fi
    return "${status}"
}

case "${1:-}" in
    --ros-only)
        run_ros_stack_in_container
        ;;
    -h|--help)
        print_usage
        ;;
    "")
        run_from_jetson_host
        ;;
    *)
        print_usage >&2
        exit 2
        ;;
esac
