uint8 HANDEDNESS_UNKNOWN=0
uint8 HANDEDNESS_LEFT=1
uint8 HANDEDNESS_RIGHT=2

std_msgs/Header header
bool detected
float32 confidence
uint8 handedness
float32 handedness_confidence
uint32 image_width
uint32 image_height
geometry_msgs/Point32[21] landmarks
