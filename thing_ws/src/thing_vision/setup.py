from setuptools import find_packages, setup

package_name = 'thing_vision'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='C103 Team',
    maintainer_email='dndwlqor@naver.com',
    description='Camera, MediaPipe landmarks, and seven-axis hand target generation.',
    license='Apache-2.0',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'camera_node = thing_vision.camera_node:main',
            'mediapipe_node = thing_vision.mediapipe_node:main',
            'world_mediapipe_node = thing_vision.world_mediapipe_node:main',
            'hand_target_node = thing_vision.hand_target_node:main',
        ],
    },
)
