from setuptools import find_packages, setup

package_name = 'thing_web_bridge'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml', 'README.md']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='C103 Team',
    maintainer_email='dndwlqor@naver.com',
    description='Validated WebSocket JSON and ROS 2 monitoring and control bridge.',
    license='Apache-2.0',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'mjpeg_streamer = thing_web_bridge.mjpeg_streamer:main',
            'web_bridge_node = thing_web_bridge.web_bridge_node:main',
        ],
    },
)
