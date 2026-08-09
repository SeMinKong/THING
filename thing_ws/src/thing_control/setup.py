from setuptools import find_packages, setup

package_name = 'thing_control'

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
    description='Command arbitration, validation, gesture, sequence, and safety control.',
    license='Apache-2.0',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'command_manager = thing_control.command_manager:main',
            'command_guard = thing_control.command_guard:main',
            'manual_executor = thing_control.manual_executor:main',
            'safety_manager = thing_control.safety_manager:main',
        ],
    },
)
