from setuptools import find_packages, setup

package_name = 'thing_teleop'

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
    description='Local keyboard teleoperation for individual logical hand axes.',
    license='Apache-2.0',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'keyboard_teleop_node = '
            'thing_teleop.keyboard_teleop_node:main',
            'keyboard_teleop_preview = thing_teleop.teleop_ui:main',
        ],
    },
)
