# Windows에서 Raspberry Pi SSH 키 인증 설정

이 문서는 Windows PC에서 Raspberry Pi에 비밀번호 없이 SSH로 접속하고, 다음 시연용
PowerShell 스크립트를 실행하기 위한 설정 방법을 설명한다.

- `exec/start_rpi_demo.ps1`: Docker와 ROS 2 bringup 실행
- `exec/reset_safety.ps1`: `/thing/reset_safety` 서비스 요청

기본 접속 정보는 다음과 같다.

```text
사용자: rpi103
Raspberry Pi IP: 192.168.100.249
```

IP 주소가 바뀌었다면 아래 명령의 주소와 PowerShell 스크립트의 `-RpiHost` 인자를
실제 주소로 변경한다.

## 1. Windows OpenSSH 확인

Windows PowerShell을 열고 다음 명령을 실행한다.

```powershell
ssh -V
```

버전 정보가 나오면 바로 다음 단계로 진행한다. 명령을 찾을 수 없다면 Windows 설정의
`시스템 > 선택적 기능`에서 **OpenSSH 클라이언트**를 설치한다.

## 2. SSH 키 생성

Windows PowerShell에서 다음 명령을 실행한다.

```powershell
ssh-keygen -t ed25519
```

저장 위치 질문에는 Enter를 눌러 기본 경로를 사용한다.

```text
C:\Users\<Windows 사용자 이름>\.ssh\id_ed25519
```

키 암호(passphrase)를 설정하면 보안은 향상되지만 스크립트를 실행할 때 키 암호 입력이
필요할 수 있다. 시연 PC에서 완전한 무인 실행이 필요하면 키 암호를 비워둘 수 있다.

생성되는 파일은 다음과 같다.

- `id_ed25519`: 개인 키. 외부에 공유하거나 저장소에 커밋하지 않는다.
- `id_ed25519.pub`: 공개 키. Raspberry Pi에 등록할 파일이다.

## 3. 공개 키를 Raspberry Pi에 등록

Windows PowerShell에서 다음 명령을 실행한다.

```powershell
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub |
  ssh rpi103@192.168.100.249 `
  "umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys"
```

처음 접속하는 장비라면 다음과 같은 호스트 확인 질문이 표시될 수 있다.

```text
Are you sure you want to continue connecting (yes/no/[fingerprint])?
```

Raspberry Pi의 주소가 맞는지 확인한 후 `yes`를 입력한다. 이어서 `rpi103` 계정의
비밀번호를 입력한다. 이 과정에서만 기존 SSH 비밀번호가 필요하다.

## 4. 비밀번호 없는 접속 확인

다음 명령으로 접속한다.

```powershell
ssh rpi103@192.168.100.249
```

비밀번호를 묻지 않고 Raspberry Pi 셸이 열리면 설정이 완료된 것이다. 접속을 종료할
때는 다음 명령을 사용한다.

```bash
exit
```

## 5. 시연 스크립트 실행

Windows PowerShell에서 프로젝트 저장소로 이동한 뒤 bringup 스크립트를 실행한다.

```powershell
powershell -ExecutionPolicy Bypass -File .\exec\start_rpi_demo.ps1
```

스크립트는 별도의 PowerShell 창을 열고 Raspberry Pi의 control stack과 motor driver
launch를 foreground로 실행한다. 새 창을 유지하면 두 launch의 로그를 함께 볼 수 있다.
시연을 종료할 때는 새 창에서 `Ctrl+C`를 누른다. 이 신호는 motor driver와 control
stack에 차례로 전달되며 Docker 컨테이너는 계속 실행된다.

기존 foreground 창이 없거나 원격 launch만 남은 경우에는 다음 종료 스크립트를
fallback으로 사용한다.

```powershell
powershell -ExecutionPolicy Bypass -File .\exec\stop_rpi_demo.ps1
```

시연 중 Safety reset이 필요하면 다음 명령을 실행한다.

```powershell
powershell -ExecutionPolicy Bypass -File .\exec\reset_safety.ps1
```

Raspberry Pi IP가 기본값과 다르면 다음처럼 전달한다.

```powershell
powershell -ExecutionPolicy Bypass -File .\exec\start_rpi_demo.ps1 `
  -RpiHost 192.168.100.249

powershell -ExecutionPolicy Bypass -File .\exec\stop_rpi_demo.ps1 `
  -RpiHost 192.168.100.249

powershell -ExecutionPolicy Bypass -File .\exec\reset_safety.ps1 `
  -RpiHost 192.168.100.249
```

## 문제 해결

### 여전히 비밀번호를 요구하는 경우

상세 접속 로그를 확인한다.

```powershell
ssh -v rpi103@192.168.100.249
```

로그에서 `Offering public key`와 `Server accepts key`가 표시되는지 확인한다.

Raspberry Pi에서 권한을 다시 설정할 수도 있다.

```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/authorized_keys
```

### 호스트 키가 변경됐다는 오류가 발생하는 경우

Raspberry Pi를 재설치했거나 같은 IP를 다른 장비가 사용하면 호스트 키 경고가 발생할
수 있다. 실제 장비가 맞는지 먼저 확인한 후 Windows에서 기존 항목을 제거한다.

```powershell
ssh-keygen -R 192.168.100.249
```

그다음 다시 SSH로 접속해 새 호스트 키를 확인하고 등록한다.

### Docker 권한 오류가 발생하는 경우

Raspberry Pi에서 `rpi103` 사용자가 Docker 그룹에 포함되어 있는지 확인한다.

```bash
id
```

출력의 그룹 목록에 `docker`가 있어야 한다. 그룹에 추가한 직후에는 로그아웃 후 다시
로그인해야 적용된다.

## 보안 주의사항

- 개인 키 `id_ed25519`는 공유하거나 Git 저장소에 커밋하지 않는다.
- 공용 PC에서는 암호 없는 개인 키를 사용하지 않는다.
- 시연이 끝난 뒤 해당 PC의 접근을 폐기하려면 Raspberry Pi의
  `~/.ssh/authorized_keys`에서 등록한 공개 키 한 줄을 제거한다.
- 비밀번호를 PowerShell 스크립트나 저장소 파일에 직접 작성하지 않는다.
