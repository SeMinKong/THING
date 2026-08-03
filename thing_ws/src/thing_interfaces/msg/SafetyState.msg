uint8 INIT=0
uint8 READY=1
uint8 RUN=2
uint8 HOLD=3
uint8 SAFE=4
uint8 FAULT=5
uint8 ESTOP=6

builtin_interfaces/Time stamp
uint8 state
bool command_timeout
bool motor_communication_ok
bool over_current
bool over_temperature
bool estop_active
uint32 fault_code
string reason
