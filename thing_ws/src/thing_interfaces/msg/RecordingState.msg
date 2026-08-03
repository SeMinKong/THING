uint8 IDLE=0
uint8 STARTING=1
uint8 RECORDING=2
uint8 STOPPING=3
uint8 COMPLETED=4
uint8 FAILED=5
uint8 INTERRUPTED=6

uint8 RESULT_UNSET=0
uint8 RESULT_SUCCESS=1
uint8 RESULT_FAILURE=2

std_msgs/Header header
uint8 state

uint64 active_session_id
string active_bag_path
builtin_interfaces/Time active_started_at

uint64 last_session_id
string last_bag_path
builtin_interfaces/Time last_started_at
builtin_interfaces/Time last_ended_at

bool result_pending
uint8 last_mimic_result
string message
